import { create, type StateCreator } from 'zustand'
import { persist } from 'zustand/middleware'
import { api } from '@/lib/api'
import type { AssetResponse } from '@/types'

const CHUNK_SIZE = 10 * 1024 * 1024 // 10 MB
const HISTORY_PAGE_SIZE = 20

// EMA smoothing factor for speed calc (0..1, higher = more responsive, less smooth).
// 0.3 strikes a balance — responsive enough to feel live, smooth enough to not jitter.
const SPEED_EMA_ALPHA = 0.3

// Minimum interval between progress UI updates (ms). XHR's onprogress fires fast;
// throttling avoids React re-renders thrashing the panel.
const PROGRESS_UPDATE_INTERVAL_MS = 200

export type UploadStatus = 'pending' | 'uploading' | 'processing' | 'complete' | 'failed' | 'cancelled'

export interface UploadFile {
  id: string
  fileName: string
  fileSize: number
  fileType: string
  projectId: string
  projectName?: string
  assetName: string
  progress: number
  processingProgress: number
  status: UploadStatus
  error?: string
  assetId?: string
  versionId?: string
  uploadId?: string
  createdAt: number // timestamp for grouping
  // Live upload telemetry (set while status === 'uploading'):
  bytesUploaded?: number  // bytes successfully PUT to S3 so far
  speedBps?: number       // EMA-smoothed upload throughput, bytes/sec
  etaSeconds?: number     // estimated seconds until upload completes
}

interface InitiateResponse {
  upload_id: string
  s3_key: string
  asset_id: string
  version_id: string
}

interface VersionInitiateResponse {
  upload_id: string
  s3_key: string
  asset_id: string
  version_id: string
}

// AbortControllers for cancellation
const abortControllers: Record<string, AbortController> = {}

interface UploadStore {
  files: UploadFile[]
  panelOpen: boolean
  historyLoaded: boolean
  historyHasMore: boolean
  historyLoading: boolean
  historySkip: number
  setPanelOpen: (open: boolean) => void
  togglePanel: () => void
  startUpload: (file: File, projectId: string, assetName: string, projectName?: string, folderId?: string | null) => string
  startVersionUpload: (file: File, assetId: string, assetName: string, projectId: string) => string
  cancelUpload: (fileId: string) => void
  retryProcessing: (fileId: string) => Promise<void>
  cancelProcessing: (fileId: string) => Promise<void>
  removeFile: (fileId: string) => void
  clearCompleted: () => void
  fetchHistory: () => Promise<void>
  fetchMoreHistory: () => Promise<void>
  // SSE-driven processing updates
  updateProcessingProgress: (assetId: string, percent: number) => void
  markProcessingComplete: (assetId: string) => void
  markProcessingFailed: (assetId: string, error: string) => void
  // Fallback poll: re-check processing items from backend (catches missed SSE events)
  refreshProcessingItems: () => Promise<void>
}

function mapProcessingStatus(status: string): UploadStatus {
  switch (status) {
    case 'uploading': return 'uploading'
    case 'processing': return 'processing'
    case 'ready': return 'complete'
    case 'failed': return 'failed'
    default: return 'complete'
  }
}

function mimeFromAssetType(assetType: string): string {
  switch (assetType) {
    case 'video': return 'video/mp4'
    case 'audio': return 'audio/mpeg'
    case 'image':
    case 'image_carousel': return 'image/jpeg'
    case 'document': return 'text/markdown'
    default: return 'application/octet-stream'
  }
}

// Document extensions we accept in v1. Mirrored on the backend in
// apps/api/schemas/upload.py:ALLOWED_DOCUMENT_EXTENSIONS.
const DOCUMENT_EXTENSIONS = ['.md', '.markdown']

/**
 * Normalise a File for upload. Browsers send no MIME (or text/plain) for .md
 * files on many platforms; we fix that here so the backend can recognise the
 * document via MIME alone. Returns the mime_type the backend should see.
 */
function deriveMimeType(file: File): string {
  if (file.type && file.type !== 'text/plain') return file.type
  const name = file.name.toLowerCase()
  if (DOCUMENT_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return 'text/markdown'
  }
  return file.type || 'application/octet-stream'
}

function mergeHistoryAssets(existing: UploadFile[], assets: AssetResponse[]): UploadFile[] {
  const existingAssetIds = new Set(existing.map((f) => f.assetId).filter(Boolean))
  const newFiles: UploadFile[] = assets
    .filter((a) => a.latest_version && !existingAssetIds.has(a.id))
    .map((a) => {
      const v = a.latest_version!
      const file = v.files?.[0]
      return {
        id: `history-${a.id}`,
        fileName: file?.original_filename ?? a.name,
        fileType: file?.mime_type ?? mimeFromAssetType(a.asset_type),
        fileSize: file?.file_size_bytes ?? 0,
        projectId: a.project_id,
        assetName: a.name,
        progress: 100,
        processingProgress: v.processing_status === 'ready' ? 100 : 0,
        status: mapProcessingStatus(v.processing_status),
        assetId: a.id,
        versionId: v.id,
        createdAt: new Date(v.created_at).getTime(),
      }
    })
  return [...existing, ...newFiles]
}

/**
 * PUT a Blob to a presigned URL with byte-level progress reporting.
 *
 * Uses XMLHttpRequest (not fetch) because only XHR exposes `upload.onprogress`
 * events for outgoing data. Resolves with the response ETag so the caller can
 * complete the S3 multipart upload.
 *
 * @param onProgress fires repeatedly during the upload with bytes sent in *this* chunk
 *                   (NOT cumulative across chunks — caller adds the offset).
 */
function putBlobWithProgress(
  url: string,
  blob: Blob,
  signal: AbortSignal,
  onProgress: (bytesSentInThisChunk: number) => void,
): Promise<{ etag: string }> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Upload cancelled', 'AbortError'))
      return
    }

    const xhr = new XMLHttpRequest()
    xhr.open('PUT', url)

    const onAbort = () => {
      xhr.abort()
      reject(new DOMException('Upload cancelled', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort, { once: true })

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded)
    }
    xhr.onload = () => {
      signal.removeEventListener('abort', onAbort)
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = xhr.getResponseHeader('ETag') ?? ''
        resolve({ etag })
      } else {
        reject(new Error(`Upload failed: ${xhr.status} ${xhr.statusText || ''}`.trim()))
      }
    }
    xhr.onerror = () => {
      signal.removeEventListener('abort', onAbort)
      reject(new Error('Network error during upload'))
    }
    xhr.ontimeout = () => {
      signal.removeEventListener('abort', onAbort)
      reject(new Error('Upload timed out'))
    }

    xhr.send(blob)
  })
}

/**
 * Tracks live upload telemetry across all parts of a single multipart upload.
 * Exposes a `record(totalBytesSentSoFar)` method that updates speed (EMA-smoothed)
 * and ETA, returning the latest values throttled by PROGRESS_UPDATE_INTERVAL_MS.
 */
function createProgressTracker(totalSize: number) {
  let smoothedBps = 0
  let lastSampleTime = Date.now()
  let lastSampleBytes = 0
  let lastUiUpdate = 0

  return {
    /**
     * Returns a patch to apply to the UploadFile, or null if it's too soon
     * since the last UI update (caller should skip the patch in that case).
     */
    sample(totalBytesSentSoFar: number): { bytesUploaded: number; speedBps: number; etaSeconds: number | undefined; progress: number } | null {
      const now = Date.now()
      const dtMs = now - lastSampleTime
      if (dtMs <= 0) return null

      // Always recompute the smoothed speed so we don't lose data between throttled updates.
      const dt = dtMs / 1000
      const deltaBytes = totalBytesSentSoFar - lastSampleBytes
      if (deltaBytes > 0 && dt > 0) {
        const instantBps = deltaBytes / dt
        smoothedBps = smoothedBps === 0
          ? instantBps
          : SPEED_EMA_ALPHA * instantBps + (1 - SPEED_EMA_ALPHA) * smoothedBps
        lastSampleTime = now
        lastSampleBytes = totalBytesSentSoFar
      }

      // Throttle UI updates to avoid React thrashing.
      if (now - lastUiUpdate < PROGRESS_UPDATE_INTERVAL_MS) return null
      lastUiUpdate = now

      const remaining = Math.max(0, totalSize - totalBytesSentSoFar)
      const etaSeconds = smoothedBps > 0 ? remaining / smoothedBps : undefined
      // Cap displayed upload progress at 95% — last 5% covers /upload/complete + processing handoff.
      const progress = Math.min(95, Math.round((totalBytesSentSoFar / totalSize) * 95))

      return { bytesUploaded: totalBytesSentSoFar, speedBps: smoothedBps, etaSeconds, progress }
    },
  }
}

const storeCreator: StateCreator<UploadStore, [['zustand/persist', unknown]]> = (set, get) => ({
  files: [],
  panelOpen: false,
  historyLoaded: false,
  historyHasMore: true,
  historyLoading: false,
  historySkip: 0,

  setPanelOpen: (open) => set({ panelOpen: open }),
  togglePanel: () => set((s) => ({ panelOpen: !s.panelOpen })),

  startUpload: (file, projectId, assetName, projectName, folderId) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`

    const entry: UploadFile = {
      id,
      fileName: file.name,
      fileSize: file.size,
      fileType: deriveMimeType(file),
      projectId,
      projectName,
      assetName,
      progress: 0,
      processingProgress: 0,
      status: 'pending',
      createdAt: Date.now(),
    }

    set((s) => ({ files: [entry, ...s.files], panelOpen: true }))

    const updateFile = (fileId: string, patch: Partial<UploadFile>) => {
      set((s) => ({
        files: s.files.map((f) => (f.id === fileId ? { ...f, ...patch } : f)),
      }))
    }

    // Start async upload
    ;(async () => {
      const controller = new AbortController()
      abortControllers[id] = controller

      // Track initiate response fields so catch block can call /upload/abort
      let upload_id: string | undefined
      let s3_key: string | undefined
      let version_id: string | undefined

      try {
        updateFile(id, { status: 'uploading', bytesUploaded: 0 })

        const initRes = await api.post<InitiateResponse>(
          '/upload/initiate',
          {
            project_id: projectId,
            asset_name: assetName,
            original_filename: file.name,
            file_size_bytes: file.size,
            mime_type: file.type,
            folder_id: folderId ?? null,
          },
        )
        upload_id = initRes.upload_id
        s3_key = initRes.s3_key
        version_id = initRes.version_id
        const asset_id = initRes.asset_id

        updateFile(id, { uploadId: upload_id, assetId: asset_id, versionId: version_id })

        const totalChunks = Math.ceil(file.size / CHUNK_SIZE)
        const parts: Array<{ PartNumber: number; ETag: string }> = []
        const tracker = createProgressTracker(file.size)
        let bytesCompleted = 0 // bytes from fully-uploaded chunks

        for (let partNumber = 1; partNumber <= totalChunks; partNumber++) {
          if (controller.signal.aborted) {
            throw new DOMException('Upload cancelled', 'AbortError')
          }

          const start = (partNumber - 1) * CHUNK_SIZE
          const end = Math.min(start + CHUNK_SIZE, file.size)
          const chunk = file.slice(start, end)
          const chunkSize = end - start

          const { presigned_url } = await api.post<{ presigned_url: string }>('/upload/presign-part', {
            s3_key,
            upload_id,
            part_number: partNumber,
          })

          const { etag } = await putBlobWithProgress(
            presigned_url,
            chunk,
            controller.signal,
            (bytesThisChunk) => {
              const patch = tracker.sample(bytesCompleted + bytesThisChunk)
              if (patch) updateFile(id, patch)
            },
          )

          parts.push({ PartNumber: partNumber, ETag: etag })
          bytesCompleted += chunkSize
        }

        await api.post('/upload/complete', {
          s3_key,
          upload_id,
          asset_id,
          version_id,
          parts,
        })

        // Upload done — backend now processes (transcode/convert).
        // For non-processable types (or if SSE isn't wired), mark complete directly.
        const isMedia = file.type.startsWith('video/') || file.type.startsWith('audio/') || file.type.startsWith('image/') || DOCUMENT_EXTENSIONS.some((e) => file.name.toLowerCase().endsWith(e))
        if (isMedia) {
          updateFile(id, {
            progress: 100,
            status: 'processing',
            processingProgress: 0,
            bytesUploaded: file.size,
            speedBps: undefined,
            etaSeconds: undefined,
          })
        } else {
          updateFile(id, {
            progress: 100,
            status: 'complete',
            bytesUploaded: file.size,
            speedBps: undefined,
            etaSeconds: undefined,
          })
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          updateFile(id, { status: 'cancelled', progress: 0, speedBps: undefined, etaSeconds: undefined })
        } else {
          const message = err instanceof Error ? err.message : 'Upload failed'
          updateFile(id, { status: 'failed', error: message, speedBps: undefined, etaSeconds: undefined })
        }
        // Notify backend so the version is marked failed (not stuck at uploading).
        // This ensures post-refresh history shows the item in "Failed", not "Active".
        if (upload_id && s3_key && version_id) {
          api.post('/upload/abort', { s3_key, upload_id, version_id }).catch(() => {})
        }
      } finally {
        delete abortControllers[id]
      }
    })()

    return id
  },

  startVersionUpload: (file, assetId, assetName, projectId) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`
    const entry: UploadFile = {
      id,
      fileName: file.name,
      fileSize: file.size,
      fileType: deriveMimeType(file),
      projectId,
      assetName,
      progress: 0,
      processingProgress: 0,
      status: 'pending',
      assetId,
      createdAt: Date.now(),
    }
    set((s) => ({ files: [entry, ...s.files], panelOpen: true }))

    const updateFile = (fileId: string, patch: Partial<UploadFile>) => {
      set((s) => ({ files: s.files.map((f) => (f.id === fileId ? { ...f, ...patch } : f)) }))
    }

    ;(async () => {
      const controller = new AbortController()
      abortControllers[id] = controller
      let upload_id: string | undefined
      let s3_key: string | undefined
      let version_id: string | undefined
      try {
        updateFile(id, { status: 'uploading', bytesUploaded: 0 })
        const initRes = await api.post<VersionInitiateResponse>(
          `/assets/${assetId}/versions`,
          {
            project_id: projectId,
            asset_name: assetName,
            original_filename: file.name,
            file_size_bytes: file.size,
            mime_type: file.type,
          },
        )
        upload_id = initRes.upload_id
        s3_key = initRes.s3_key
        version_id = initRes.version_id
        updateFile(id, { uploadId: upload_id, versionId: version_id })

        const totalChunks = Math.ceil(file.size / CHUNK_SIZE)
        const parts: Array<{ PartNumber: number; ETag: string }> = []
        const tracker = createProgressTracker(file.size)
        let bytesCompleted = 0

        for (let partNumber = 1; partNumber <= totalChunks; partNumber++) {
          if (controller.signal.aborted) throw new DOMException('Upload cancelled', 'AbortError')
          const start = (partNumber - 1) * CHUNK_SIZE
          const end = Math.min(start + CHUNK_SIZE, file.size)
          const chunk = file.slice(start, end)
          const chunkSize = end - start
          const { presigned_url } = await api.post<{ presigned_url: string }>('/upload/presign-part', {
            s3_key, upload_id, part_number: partNumber,
          })
          const { etag } = await putBlobWithProgress(
            presigned_url,
            chunk,
            controller.signal,
            (bytesThisChunk) => {
              const patch = tracker.sample(bytesCompleted + bytesThisChunk)
              if (patch) updateFile(id, patch)
            },
          )
          parts.push({ PartNumber: partNumber, ETag: etag })
          bytesCompleted += chunkSize
        }

        await api.post('/upload/complete', { s3_key, upload_id, asset_id: assetId, version_id, parts })
        const isMedia = file.type.startsWith('video/') || file.type.startsWith('audio/') || file.type.startsWith('image/') || DOCUMENT_EXTENSIONS.some((e) => file.name.toLowerCase().endsWith(e))
        updateFile(id, {
          progress: 100,
          status: isMedia ? 'processing' : 'complete',
          processingProgress: 0,
          bytesUploaded: file.size,
          speedBps: undefined,
          etaSeconds: undefined,
        })
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          updateFile(id, { status: 'cancelled', progress: 0, speedBps: undefined, etaSeconds: undefined })
        } else {
          updateFile(id, { status: 'failed', error: err instanceof Error ? err.message : 'Upload failed', speedBps: undefined, etaSeconds: undefined })
        }
        if (upload_id && s3_key && version_id) {
          api.post('/upload/abort', { s3_key, upload_id, version_id }).catch(() => {})
        }
      } finally {
        delete abortControllers[id]
      }
    })()

    return id
  },

  cancelUpload: (fileId) => {
    abortControllers[fileId]?.abort()
    set((s) => ({
      files: s.files.map((f) =>
        f.id === fileId ? { ...f, status: 'cancelled' as const, progress: 0, speedBps: undefined, etaSeconds: undefined } : f,
      ),
    }))
  },

  retryProcessing: async (fileId) => {
    const file = get().files.find((f) => f.id === fileId)
    if (!file?.assetId || !file?.versionId) return
    // Optimistically flip to processing so the UI updates immediately.
    set((s) => ({
      files: s.files.map((f) =>
        f.id === fileId
          ? { ...f, status: 'processing' as const, processingProgress: 0, error: undefined }
          : f,
      ),
    }))
    try {
      await api.post(`/assets/${file.assetId}/versions/${file.versionId}/retry-processing`, {})
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Retry failed'
      set((s) => ({
        files: s.files.map((f) => (f.id === fileId ? { ...f, status: 'failed' as const, error: message } : f)),
      }))
    }
  },

  cancelProcessing: async (fileId) => {
    const file = get().files.find((f) => f.id === fileId)
    if (!file?.assetId || !file?.versionId) return
    // Optimistic update — call backend, then sync.
    set((s) => ({
      files: s.files.map((f) =>
        f.id === fileId ? { ...f, status: 'failed' as const, error: 'Cancelled by user' } : f,
      ),
    }))
    try {
      await api.post(`/assets/${file.assetId}/versions/${file.versionId}/cancel-processing`, {})
    } catch {
      // If backend rejects, the next refreshProcessingItems will reconcile.
    }
  },

  removeFile: (fileId) => {
    set((s) => ({ files: s.files.filter((f) => f.id !== fileId) }))
  },

  clearCompleted: () => {
    set((s) => ({ files: s.files.filter((f) => f.status !== 'complete') }))
  },

  fetchHistory: async () => {
    if (get().historyLoaded) return
    set({ historyLoading: true })
    try {
      const assets = await api.get<AssetResponse[]>(`/me/assets?skip=0&limit=${HISTORY_PAGE_SIZE}`)
      const merged = mergeHistoryAssets(get().files, assets)
      set({
        historyLoaded: true,
        historyLoading: false,
        historySkip: HISTORY_PAGE_SIZE,
        historyHasMore: assets.length >= HISTORY_PAGE_SIZE,
        files: merged,
      })
    } catch {
      set({ historyLoaded: true, historyLoading: false })
    }
  },

  fetchMoreHistory: async () => {
    const { historyHasMore, historyLoading, historySkip } = get()
    if (!historyHasMore || historyLoading) return
    set({ historyLoading: true })
    try {
      const assets = await api.get<AssetResponse[]>(`/me/assets?skip=${historySkip}&limit=${HISTORY_PAGE_SIZE}`)
      const merged = mergeHistoryAssets(get().files, assets)
      set((s) => ({
        historyLoading: false,
        historySkip: s.historySkip + HISTORY_PAGE_SIZE,
        historyHasMore: assets.length >= HISTORY_PAGE_SIZE,
        files: merged,
      }))
    } catch {
      set({ historyLoading: false })
    }
  },

  updateProcessingProgress: (assetId, percent) => {
    set((s) => ({
      files: s.files.map((f) =>
        f.assetId === assetId && f.status === 'processing'
          ? { ...f, processingProgress: percent }
          : f,
      ),
    }))
  },

  markProcessingComplete: (assetId) => {
    set((s) => ({
      files: s.files.map((f) =>
        f.assetId === assetId && f.status === 'processing'
          ? { ...f, status: 'complete' as const, processingProgress: 100 }
          : f,
      ),
    }))
  },

  markProcessingFailed: (assetId, error) => {
    set((s) => ({
      files: s.files.map((f) =>
        f.assetId === assetId && f.status === 'processing'
          ? { ...f, status: 'failed' as const, error }
          : f,
      ),
    }))
  },

  refreshProcessingItems: async () => {
    const processingFiles = get().files.filter((f) => f.status === 'processing' && f.assetId)
    if (!processingFiles.length) return
    try {
      const results = await Promise.all(
        processingFiles.map((f) =>
          api.get<AssetResponse>(`/assets/${f.assetId}`).catch(() => null),
        ),
      )
      set((s) => ({
        files: s.files.map((f) => {
          if (f.status !== 'processing' || !f.assetId) return f
          const idx = processingFiles.findIndex((pf) => pf.assetId === f.assetId)
          const asset = idx >= 0 ? results[idx] : null
          if (!asset?.latest_version) return f
          const status = mapProcessingStatus(asset.latest_version.processing_status)
          if (status === 'processing') return f
          return { ...f, status, processingProgress: status === 'complete' ? 100 : 0 }
        }),
      }))
    } catch {
      // SSE is the primary mechanism; ignore poll errors
    }
  },
})

export const useUploadStore = create<UploadStore>()(
  persist(storeCreator, {
    name: 'ff-uploads',
    // Only persist failed/cancelled items — in-progress uploads can't be resumed
    // and successful ones are fetched from the API history on panel open.
    partialize: (state: UploadStore) => ({
      files: state.files.filter(
        (f: UploadFile) => f.status === 'failed' || f.status === 'cancelled',
      ),
    }),
  }),
)
