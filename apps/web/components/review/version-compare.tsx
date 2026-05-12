'use client'

/**
 * VersionCompare — synced dual-frame video compare.
 *
 * Producer-driven UX: when reviewing v3, compare against v2 (or v1) without
 * scrubbing back and forth in a single player. Both videos play in lock-step
 * — one play/pause button, one progress bar, one current-time readout.
 *
 * Architecture:
 *
 *   ┌─────────────────────────────────────────────────────┐
 *   │ Compare header (close, label)                        │
 *   ├─────────────────────────────────┬───────────────────┤
 *   │ Left version picker             │ Right version pick│
 *   │ ┌─────────────────────────────┐ │ ┌───────────────┐ │
 *   │ │                             │ │ │               │ │
 *   │ │       <video> (primary)     │ │ │   <video>     │ │
 *   │ │                             │ │ │               │ │
 *   │ └─────────────────────────────┘ │ └───────────────┘ │
 *   ├─────────────────────────────────┴───────────────────┤
 *   │  ▶  ━━━━━━━●━━━━━━━━━━━━━  0:34 / 8:02              │
 *   └─────────────────────────────────────────────────────┘
 *
 * Sync rules:
 *   * Play/Pause/Seek/Rate on the PRIMARY (left) video are mirrored to the
 *     secondary in real time via direct ref calls. Listening to the
 *     primary's native events keeps the secondary in lock-step without
 *     introducing a global state store just for this view.
 *   * Drift correction every 500ms: if abs(left - right) > 0.3s, snap
 *     secondary to primary. Cheap and good enough — browsers don't
 *     guarantee perfect simultaneous playback across two <video> elements.
 *   * Duration in the control bar is min(left.duration, right.duration).
 *     If one cut is shorter, the secondary just stops naturally; the
 *     primary's timeupdate still drives the slider.
 *
 * HLS:
 *   Both stream URLs go through the existing useVideoPlayer hook so hls.js
 *   bootstraps each video element. We attach a 'sync controller' ref that
 *   wraps both useVideoPlayer returns into the synced control surface.
 */
import * as React from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import {
  X,
  ChevronDown,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Play,
  Pause,
} from 'lucide-react'
import { cn, formatTime } from '@/lib/utils'
import { useVideoPlayer } from '@/hooks/use-video-player'
import { api } from '@/lib/api'
import type { AssetVersion, AssetVersionStatus } from '@/types'

interface Props {
  assetId: string
  versions: AssetVersion[]
  primaryVersionId: string
  onClose: () => void
}

const statusCfg: Record<
  AssetVersionStatus,
  { label: string; className: string; icon: React.ReactNode }
> = {
  uploading: { label: 'Uploading', className: 'text-status-info', icon: <Loader2 className="h-2.5 w-2.5 animate-spin" /> },
  processing: { label: 'Processing', className: 'text-status-warning', icon: <Loader2 className="h-2.5 w-2.5 animate-spin" /> },
  ready: { label: 'Ready', className: 'text-status-success', icon: <CheckCircle2 className="h-2.5 w-2.5" /> },
  failed: { label: 'Failed', className: 'text-status-error', icon: <AlertCircle className="h-2.5 w-2.5" /> },
}

export function VersionCompare({ assetId, versions, primaryVersionId, onClose }: Props) {
  const sorted = React.useMemo(
    () => [...versions].sort((a, b) => a.version_number - b.version_number),
    [versions],
  )

  const defaultLeft = sorted.find((v) => v.id === primaryVersionId) ?? sorted[sorted.length - 1]
  const priorReady = [...sorted].reverse().find(
    (v) => v.id !== defaultLeft.id && v.processing_status === 'ready',
  )
  const defaultRight = priorReady ?? sorted[0]

  const [leftVersion, setLeftVersion] = React.useState<AssetVersion>(defaultLeft)
  const [rightVersion, setRightVersion] = React.useState<AssetVersion>(defaultRight)

  // Stream URL pre-fetch per side (so useVideoPlayer gets a concrete URL).
  const leftUrl = useStreamUrl(assetId, leftVersion.id)
  const rightUrl = useStreamUrl(assetId, rightVersion.id)

  // useVideoPlayer manages HLS bootstrap + native events + currentTime
  // tracking for each video element. Each side gets its own instance —
  // they don't talk to each other through the hook; sync is done by us
  // below via direct .play()/.pause()/.seek() on the secondary.
  const left = useVideoPlayer(leftUrl)
  const right = useVideoPlayer(rightUrl)

  // ── Sync: mirror primary -> secondary on play/pause/seek/rate ───────────
  React.useEffect(() => {
    if (left.isPlaying && !right.isPlaying) right.play()
    if (!left.isPlaying && right.isPlaying) right.pause()
  }, [left.isPlaying, right])

  React.useEffect(() => {
    right.setPlaybackRate(left.playbackRate)
  }, [left.playbackRate, right])

  // Drift correction: every 500ms, if secondary has slipped >300ms,
  // snap it back to primary. Browsers don't guarantee tight cross-element
  // sync — this keeps things visually aligned without re-seeking on every
  // tick.
  React.useEffect(() => {
    const id = setInterval(() => {
      const drift = Math.abs(left.currentTime - right.currentTime)
      if (drift > 0.3) {
        right.seek(left.currentTime)
      }
    }, 500)
    return () => clearInterval(id)
  }, [left, right])

  const togglePlay = () => {
    if (left.isPlaying) {
      left.pause()
      right.pause()
    } else {
      left.play()
      right.play()
    }
  }

  const handleSeek = (t: number) => {
    left.seek(t)
    right.seek(t)
  }

  // Use the shorter of the two durations so the slider stays valid for both.
  // If one video is still loading duration metadata, fall back to the other.
  const duration = Math.min(left.duration || Infinity, right.duration || Infinity)
  const safeDuration = Number.isFinite(duration) ? duration : 0

  return (
    <div className="absolute inset-0 z-30 flex flex-col bg-bg-primary">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between px-3 h-10 border-b border-border bg-bg-secondary">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-text-primary font-medium">Compare</span>
          <span className="text-text-tertiary">— synced playback, scrub once</span>
        </div>
        <button
          onClick={onClose}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
        >
          <X className="h-3.5 w-3.5" />
          Exit compare
        </button>
      </div>

      {/* Dual frames */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0">
        <CompareFrame
          label="Left"
          versions={sorted}
          selected={leftVersion}
          onSelect={setLeftVersion}
          videoRef={left.videoRef}
          streamUrl={leftUrl}
          isLoading={left.isLoading}
          error={left.error}
          className="md:border-r border-border"
        />
        <CompareFrame
          label="Right"
          versions={sorted}
          selected={rightVersion}
          onSelect={setRightVersion}
          videoRef={right.videoRef}
          streamUrl={rightUrl}
          isLoading={right.isLoading}
          error={right.error}
          className="md:border-l-0 border-t md:border-t-0 border-border"
        />
      </div>

      {/* Shared control bar */}
      <div className="shrink-0 border-t border-border bg-bg-secondary px-3 py-2">
        <div className="flex items-center gap-3">
          <button
            onClick={togglePlay}
            disabled={!leftUrl || !rightUrl}
            className="flex items-center justify-center h-9 w-9 rounded-full bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-40"
            aria-label={left.isPlaying ? 'Pause' : 'Play'}
          >
            {left.isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 ml-0.5" />}
          </button>

          {/* Scrub bar */}
          <input
            type="range"
            min={0}
            max={safeDuration || 0}
            step={0.01}
            value={left.currentTime}
            onChange={(e) => handleSeek(Number(e.target.value))}
            className="flex-1 h-1.5 rounded-full bg-bg-tertiary accent-[var(--accent)] cursor-pointer"
          />

          <div className="tabular-nums text-xs text-text-secondary shrink-0 min-w-[90px] text-right">
            {formatTime(left.currentTime)} / {formatTime(safeDuration)}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── CompareFrame: bare video element + version picker, no controls ──────────

interface FrameProps {
  label: string
  versions: AssetVersion[]
  selected: AssetVersion
  onSelect: (v: AssetVersion) => void
  videoRef: React.RefObject<HTMLVideoElement>
  streamUrl: string | null
  isLoading: boolean
  error: string | null
  className?: string
}

function CompareFrame({
  label,
  versions,
  selected,
  onSelect,
  videoRef,
  streamUrl,
  isLoading,
  error,
  className,
}: FrameProps) {
  return (
    <div className={cn('flex-1 flex flex-col min-h-0 min-w-0', className)}>
      <div className="shrink-0 flex items-center justify-between px-3 h-9 border-b border-border bg-bg-secondary/50">
        <span className="text-[11px] uppercase tracking-wider text-text-tertiary">{label}</span>
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className="inline-flex items-center gap-1 rounded-md px-2 py-1 bg-accent text-white text-xs font-medium hover:bg-accent/90 transition-colors outline-none">
              v{selected.version_number}
              <ChevronDown className="h-3 w-3 opacity-70" />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              align="end"
              sideOffset={6}
              className="z-[200] min-w-[160px] rounded-xl border border-border bg-bg-elevated shadow-2xl py-1.5"
            >
              {versions.map((v) => {
                const isActive = v.id === selected.id
                const cfg = statusCfg[v.processing_status]
                const disabled = v.processing_status === 'uploading' || v.processing_status === 'processing'
                return (
                  <DropdownMenu.Item
                    key={v.id}
                    disabled={disabled}
                    onSelect={() => onSelect(v)}
                    className={cn(
                      'flex items-center justify-between gap-3 mx-1 px-2.5 py-2 rounded-lg text-sm cursor-pointer outline-none transition-colors',
                      isActive
                        ? 'bg-accent/10 text-accent font-medium'
                        : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary',
                      disabled && 'opacity-50 cursor-not-allowed',
                    )}
                  >
                    <span>v{v.version_number}</span>
                    <span className={cn('inline-flex items-center gap-1 text-[11px]', cfg.className)}>
                      {cfg.icon}
                      {cfg.label}
                    </span>
                  </DropdownMenu.Item>
                )
              })}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>

      <div className="flex-1 relative bg-black flex items-center justify-center min-h-0">
        {/* The <video> element. Sound disabled by default — two players
            simultaneously playing audio is jarring. Producer can unmute on
            one side manually if needed; mute persists per element. */}
        <video
          ref={videoRef}
          className="max-h-full max-w-full"
          playsInline
          muted
        />

        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <Loader2 className="h-6 w-6 animate-spin text-zinc-500" />
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60">
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        )}

        {/* Version badge on the frame */}
        <div className="absolute top-3 left-3 inline-flex items-center gap-1 rounded-md bg-black/60 backdrop-blur px-2 py-1 text-[11px] font-medium text-white pointer-events-none">
          v{selected.version_number}
        </div>
      </div>
    </div>
  )
}

// ── Stream URL pre-fetch hook ───────────────────────────────────────────────

function useStreamUrl(assetId: string, versionId: string): string | null {
  const [url, setUrl] = React.useState<string | null>(null)
  React.useEffect(() => {
    let cancelled = false
    setUrl(null)
    api
      .get<{ url: string }>(`/assets/${assetId}/stream?version_id=${versionId}`)
      .then((data) => {
        if (cancelled) return
        const resolved = data.url.startsWith('/')
          ? `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}${data.url}`
          : data.url
        setUrl(resolved)
      })
      .catch(() => {
        /* error surfaces via useVideoPlayer once it has no src */
      })
    return () => {
      cancelled = true
    }
  }, [assetId, versionId])
  return url
}
