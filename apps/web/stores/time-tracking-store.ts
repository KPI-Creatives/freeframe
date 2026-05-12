import { create } from 'zustand'

/**
 * Prompt queue for the post-upload Time Tracking modal.
 *
 * The upload-store enqueues one of these every time an upload finishes on an
 * asset where `track_time === true`. A single page-level mount of
 * `<TimeTrackingPromptHost>` reads `current` and renders the modal. When the
 * editor saves (or skips), the host calls `consume()` to advance to the next
 * prompt in the queue.
 *
 * Queue rather than single-slot so back-to-back uploads (v2 then v3 on the
 * same asset, or two different assets in sequence) don't clobber each other —
 * the modal cycles through them one at a time.
 */
export interface TimeTrackingPrompt {
  assetId: string
  versionId: string
  assetName: string
  versionNumber: number
  /**
   * Total minutes already accumulated on the asset BEFORE this version's
   * entry. Used for the "Already on this asset" line in the modal so the
   * editor sees what their entry will add to.
   */
  priorTotalMinutes: number
  /** Existing minutes_spent on this specific version (if it's a re-prompt). */
  initialMinutes: number | null
}

interface TimeTrackingStore {
  queue: TimeTrackingPrompt[]
  /** True when the host modal is open. Set by the host on mount/dismiss. */
  isOpen: boolean
  current: () => TimeTrackingPrompt | null
  enqueue: (prompt: TimeTrackingPrompt) => void
  /** Pop the head and close the modal until the next render cycle. */
  consume: () => void
  /** Manually open a prompt (used by the Fields-tab "edit" affordance). */
  openForEdit: (prompt: TimeTrackingPrompt) => void
  setOpen: (open: boolean) => void
  /** Replace minutes on a specific queued/open prompt — used after a successful
   *  patch so the modal text reflects what's in the DB. */
  updateInitial: (versionId: string, minutes: number | null) => void
}

export const useTimeTrackingStore = create<TimeTrackingStore>((set, get) => ({
  queue: [],
  isOpen: false,
  current: () => get().queue[0] ?? null,
  enqueue: (prompt) => {
    set((s) => {
      // Dedup by versionId — if the same version is already queued, replace
      // its payload (newer data wins) rather than queueing twice.
      const idx = s.queue.findIndex((p) => p.versionId === prompt.versionId)
      if (idx >= 0) {
        const next = [...s.queue]
        next[idx] = prompt
        return { queue: next, isOpen: true }
      }
      return { queue: [...s.queue, prompt], isOpen: true }
    })
  },
  consume: () => {
    set((s) => {
      const next = s.queue.slice(1)
      return { queue: next, isOpen: next.length > 0 }
    })
  },
  openForEdit: (prompt) => {
    set((s) => ({ queue: [prompt, ...s.queue], isOpen: true }))
  },
  setOpen: (open) => set({ isOpen: open }),
  updateInitial: (versionId, minutes) => {
    set((s) => ({
      queue: s.queue.map((p) =>
        p.versionId === versionId ? { ...p, initialMinutes: minutes } : p,
      ),
    }))
  },
}))
