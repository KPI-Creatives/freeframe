'use client'

/**
 * Mark-Delivered confirmation dialog. Simpler than Send-to-Client — no email,
 * no new share link, just a phase flip and a share-link permission downgrade.
 *
 * Visible only to producer+ users. The backend gate enforces this regardless.
 */
import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, PackageCheck, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

interface Props {
  assetId: string
  assetName: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess?: () => void
}

interface DeliveredResponse {
  asset_id: string
  phase: string
  phase_delivered_at: string | null
  delivered_version_id: string | null
  share_links_downgraded: number
}

export function MarkDeliveredDialog({
  assetId,
  assetName,
  open,
  onOpenChange,
  onSuccess,
}: Props) {
  const [submitting, setSubmitting] = React.useState(false)
  const [result, setResult] = React.useState<DeliveredResponse | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (open) {
      setSubmitting(false)
      setResult(null)
      setError(null)
    }
  }, [open])

  const handleConfirm = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.post<DeliveredResponse>(`/assets/${assetId}/mark-delivered`, {})
      setResult(res)
      onSuccess?.()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Mark delivered failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(92vw,460px)] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-bg-secondary p-6 shadow-2xl',
          )}
        >
          <div className="flex items-start justify-between gap-3 mb-3">
            <div>
              <Dialog.Title className="text-lg font-semibold text-text-primary">
                Mark delivered
              </Dialog.Title>
              <Dialog.Description className="text-xs text-text-tertiary mt-0.5">
                {assetName}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button className="rounded-md p-1 text-text-tertiary hover:bg-bg-hover hover:text-text-primary">
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          {result ? (
            <div className="space-y-3">
              <div className="rounded-md bg-status-success/10 border border-status-success/30 p-3">
                <p className="text-sm text-status-success font-medium">
                  ✓ Marked delivered
                </p>
                <p className="text-xs text-text-secondary mt-1">
                  Phase is now <strong>delivered</strong>. Any active share-links
                  have been downgraded to view-only ({result.share_links_downgraded}
                  {' '}updated).
                </p>
              </div>
              <div className="flex justify-end">
                <Button onClick={() => onOpenChange(false)}>Done</Button>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-text-secondary">
                This will:
              </p>
              <ul className="space-y-1 pl-4 text-xs text-text-secondary list-disc">
                <li>Set the asset's phase to <strong>delivered</strong>.</li>
                <li>Snapshot the current latest version as the delivered version.</li>
                <li>Downgrade any active share-links to view-only — the client signed off.</li>
              </ul>
              <p className="text-xs text-amber-400">
                Phase transitions are one-way. You cannot move back to client review
                after marking delivered.
              </p>

              {error && <p className="text-xs text-status-error">{error}</p>}

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
                  Cancel
                </Button>
                <Button onClick={handleConfirm} disabled={submitting}>
                  {submitting ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> Marking…
                    </>
                  ) : (
                    <>
                      <PackageCheck className="h-3.5 w-3.5 mr-1.5" /> Confirm delivery
                    </>
                  )}
                </Button>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
