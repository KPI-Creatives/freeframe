'use client'

/**
 * TimeTrackingModal — post-upload prompt: "how long did this version take?"
 *
 * Shown after an upload completes on an asset where `track_time === true`.
 * Editor picks a preset, types a custom h:m value, or skips. On Log,
 * `POST /assets/:id/versions/:vid/log-time` is called and the modal advances
 * to the next prompt in the queue (or closes).
 *
 * Presets (locked with the user):
 *   5m · 10m · 15m · 30m · 45m
 *   1h · 1h30m · 2h · 3h · 8h
 *
 * Custom: h-int + m-int, snapped to 5-minute step on commit.
 *
 * The "Already on this asset" line shows the prior total (sum of OTHER
 * versions) and previews what the new total will be after this entry lands.
 * Re-edit semantics: if `initialMinutes != null` (Fields-tab edit), the
 * preview accounts for replacing the version's existing value, not adding to
 * it. The store passes `priorTotalMinutes` as "total of OTHER versions"
 * (i.e. excludes this one); preview = priorTotal + selected.
 */

import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Clock, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { useTimeTrackingStore } from '@/stores/time-tracking-store'
import { mutate as globalMutate } from 'swr'

// Locked preset list. Ordering and values match the user's spec; we don't
// inject anything between 30m and 1h because the next preset that gets a
// row-fit advantage is 45m.
const PRESETS = [5, 10, 15, 30, 45, 60, 90, 120, 180, 480]

function formatPreset(min: number): string {
  if (min < 60) return `${min}m`
  const h = Math.floor(min / 60)
  const m = min % 60
  if (m === 0) return `${h}h`
  return `${h}h${m}m`
}

function formatTotal(min: number): string {
  if (min <= 0) return '0m'
  const h = Math.floor(min / 60)
  const m = min % 60
  if (h === 0) return `${m}m`
  if (m === 0) return `${h}h`
  return `${h}h ${m}m`
}

// Snap arbitrary input to the nearest 5-minute step. Both the API and the
// DB CHECK constraint require this — we'd rather sanitise on client than
// let the editor see a 422.
function snapTo5(min: number): number {
  if (min < 0) return 0
  return Math.round(min / 5) * 5
}

export function TimeTrackingPromptHost() {
  const prompt = useTimeTrackingStore((s) => s.queue[0] ?? null)
  const isOpen = useTimeTrackingStore((s) => s.isOpen)
  const consume = useTimeTrackingStore((s) => s.consume)
  const setOpen = useTimeTrackingStore((s) => s.setOpen)

  if (!prompt) return null

  return (
    <TimeTrackingModal
      key={prompt.versionId}
      prompt={prompt}
      open={isOpen}
      onClose={() => {
        // Closing without saving = treat as Skip. Pop the head; the next
        // queued prompt (if any) will render via the key change.
        consume()
      }}
      onSaved={() => {
        consume()
        // After a successful log, the asset detail / version switcher need
        // to reflect the new minutes_spent + total. SWR keys aren't
        // universally used (some pages hold the asset in useState via
        // ReviewProvider), so we just nudge the standard ones — the rest
        // will re-render on their next focus or navigation.
        globalMutate(`/assets/${prompt.assetId}`).catch(() => {})
        globalMutate(`/assets/${prompt.assetId}/versions`).catch(() => {})
      }}
    />
  )
}

interface ModalProps {
  prompt: import('@/stores/time-tracking-store').TimeTrackingPrompt
  open: boolean
  onClose: () => void
  onSaved: () => void
}

function TimeTrackingModal({ prompt, open, onClose, onSaved }: ModalProps) {
  const { assetId, versionId, assetName, versionNumber, priorTotalMinutes, initialMinutes } = prompt

  // Selected minutes. null = nothing chosen yet; we won't show the "Log" CTA
  // text until something is picked. When `initialMinutes != null` (re-edit
  // from Fields tab), seed with that value so the editor sees their current
  // entry highlighted.
  const [selectedMin, setSelectedMin] = React.useState<number | null>(
    initialMinutes ?? null,
  )

  // Custom input state. Kept separate from `selectedMin` so the editor can
  // type without committing; commits on blur / Enter / preset click resets.
  const [customH, setCustomH] = React.useState<string>('')
  const [customM, setCustomM] = React.useState<string>('')

  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const commitCustom = React.useCallback(() => {
    const h = parseInt(customH || '0', 10)
    const m = parseInt(customM || '0', 10)
    if (Number.isNaN(h) || Number.isNaN(m)) return
    const raw = h * 60 + m
    if (raw === 0) {
      setSelectedMin(0)
      return
    }
    setSelectedMin(snapTo5(raw))
  }, [customH, customM])

  // When user clicks a preset, the custom inputs clear — only one source of
  // truth at a time. Keeps the CTA label honest about what will be saved.
  const pickPreset = (min: number) => {
    setSelectedMin(min)
    setCustomH('')
    setCustomM('')
  }

  // "Total after this" preview. priorTotalMinutes is "total of OTHER
  // versions" (passed in by the queue producer). If this is a re-edit and
  // selectedMin === initialMinutes, the preview equals current asset total.
  const previewTotal = priorTotalMinutes + (selectedMin ?? 0)

  const handleLog = async () => {
    if (selectedMin === null) return
    setSubmitting(true)
    setError(null)
    try {
      await api.post(`/assets/${assetId}/versions/${versionId}/log-time`, {
        minutes_spent: selectedMin,
      })
      onSaved()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to log time'
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  const handleSkip = async () => {
    // Skip = also a save, but minutes_spent: null. Lets a producer later see
    // "this version was prompted but the editor declined to enter time" vs.
    // "this version was never prompted at all" (which only happens when the
    // modal was closed without ever interacting). We post null on the
    // explicit Skip button; the X close button just dismisses without
    // calling the endpoint.
    setSubmitting(true)
    setError(null)
    try {
      await api.post(`/assets/${assetId}/versions/${versionId}/log-time`, {
        minutes_spent: null,
      })
      onSaved()
    } catch (e: unknown) {
      // If the skip POST fails, fall back to a silent dismiss — we don't
      // want the editor stuck behind a modal because of a transient API hiccup.
      onClose()
    } finally {
      setSubmitting(false)
    }
  }

  const ctaLabel =
    selectedMin === null
      ? 'Pick a value'
      : selectedMin === 0
        ? 'Log 0m'
        : `Log ${formatPreset(selectedMin)}`

  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(440px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-bg-elevated shadow-2xl p-5 focus:outline-none"
          onEscapeKeyDown={onClose}
          onPointerDownOutside={(e) => {
            // Don't dismiss on outside click — too easy to lose work. Editor
            // has to explicitly Skip or X. The Escape key still works as a
            // close shortcut (handled by Radix).
            e.preventDefault()
          }}
        >
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2">
              <Clock className="h-4 w-4 text-text-secondary" />
              <Dialog.Title className="text-sm font-semibold text-text-primary">
                How long did this take?
              </Dialog.Title>
            </div>
            <button
              onClick={onClose}
              disabled={submitting}
              className="text-text-tertiary hover:text-text-primary p-1 rounded-md hover:bg-bg-hover transition-colors"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <Dialog.Description className="text-xs text-text-secondary mb-4">
            {assetName} <span className="text-text-tertiary">·</span> v{versionNumber}
          </Dialog.Description>

          {/* Preset grid: 2 rows × 5 columns. Buttons are equal-width via
              CSS grid so the longest label (1h30m) doesn't push neighbours. */}
          <div className="grid grid-cols-5 gap-1.5 mb-3">
            {PRESETS.map((min) => {
              const active = selectedMin === min && customH === '' && customM === ''
              return (
                <button
                  key={min}
                  type="button"
                  onClick={() => pickPreset(min)}
                  disabled={submitting}
                  className={cn(
                    'rounded-md px-2 py-2 text-xs font-medium transition-colors border',
                    active
                      ? 'bg-[var(--accent)] border-[var(--accent)] text-white'
                      : 'border-border text-text-secondary hover:text-text-primary hover:bg-bg-hover',
                  )}
                >
                  {formatPreset(min)}
                </button>
              )
            })}
          </div>

          {/* Custom h:m. The 5-minute snap happens on blur, not on every
              keystroke, so typing 17 doesn't visually jump to 15 mid-edit. */}
          <div className="flex items-center gap-2 mb-4 text-xs text-text-secondary">
            <span>Or custom:</span>
            <input
              type="number"
              min={0}
              max={24}
              value={customH}
              onChange={(e) => setCustomH(e.target.value)}
              onBlur={commitCustom}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  commitCustom()
                  e.preventDefault()
                }
              }}
              placeholder="0"
              disabled={submitting}
              className="w-14 rounded-md border border-border bg-bg-primary px-2 py-1 text-text-primary text-center tabular-nums focus:border-[var(--accent)] outline-none"
            />
            <span>h</span>
            <input
              type="number"
              min={0}
              max={55}
              step={5}
              value={customM}
              onChange={(e) => setCustomM(e.target.value)}
              onBlur={commitCustom}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  commitCustom()
                  e.preventDefault()
                }
              }}
              placeholder="0"
              disabled={submitting}
              className="w-14 rounded-md border border-border bg-bg-primary px-2 py-1 text-text-primary text-center tabular-nums focus:border-[var(--accent)] outline-none"
            />
            <span>m</span>
            {selectedMin !== null && (customH !== '' || customM !== '') && (
              <span className="ml-1 text-text-tertiary text-[11px]">
                = {formatPreset(selectedMin)}
              </span>
            )}
          </div>

          {/* Total preview block */}
          <div className="rounded-md bg-bg-secondary/50 border border-border px-3 py-2 mb-4 text-xs">
            <div className="flex justify-between text-text-secondary">
              <span>Already on this asset</span>
              <span className="tabular-nums">{formatTotal(priorTotalMinutes)}</span>
            </div>
            <div className="flex justify-between mt-1 text-text-primary font-medium">
              <span>Total after this</span>
              <span className="tabular-nums">{formatTotal(previewTotal)}</span>
            </div>
          </div>

          {error && (
            <p className="text-xs text-status-error mb-3">{error}</p>
          )}

          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={handleSkip}
              disabled={submitting}
              className="text-xs text-text-tertiary hover:text-text-secondary disabled:opacity-50"
            >
              Skip
            </button>
            <button
              type="button"
              onClick={handleLog}
              disabled={submitting || selectedMin === null}
              className="inline-flex items-center rounded-md bg-[var(--accent)] text-white text-xs font-medium px-4 py-2 hover:opacity-90 transition-opacity disabled:opacity-40"
            >
              {submitting ? 'Saving…' : ctaLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

// ── Tiny helper exposed for use in version switcher / fields tab ─────────────

/** Display a minutes count in compact form. Re-export so callers don't have
 *  to duplicate the conversion. */
export function formatMinutes(min: number | null | undefined): string {
  if (!min || min <= 0) return ''
  return formatTotal(min)
}
