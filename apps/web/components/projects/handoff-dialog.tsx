'use client'

/**
 * Upload Hand-off dialog.
 *
 * After an editor uploads a new version, this modal asks "ready for review?"
 * and lets them pick a producer to hand the asset off to. On confirm:
 *
 *   - PATCH /assets/{id} with the chosen producer as assignee_id and
 *     status='in_review' so the asset surfaces in the producer's queue.
 *   - Modal closes; toast confirms the hand-off.
 *
 * If skipped, the asset stays on the editor (they're still iterating). If
 * the project has zero producer-or-above members, the modal does not render
 * at all — there's no one to hand off TO.
 */
import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, Send, Loader2 } from 'lucide-react'
import useSWR from 'swr'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { ProjectMember, User, UserRole } from '@/types'

interface Props {
  assetId: string
  projectId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onHandedOff?: (producerId: string) => void
}

export function HandoffDialog({
  assetId,
  projectId,
  open,
  onOpenChange,
  onHandedOff,
}: Props) {
  const { data: members } = useSWR<ProjectMember[]>(
    open ? `/projects/${projectId}/members` : null,
    () => api.get<ProjectMember[]>(`/projects/${projectId}/members`),
  )

  // Fetch users only when we have member ids. The /users endpoint already
  // takes a comma-separated ids query param (see apps/api/routers/users.py).
  const memberIds = (members ?? []).map((m) => m.user_id).filter(Boolean)
  const { data: users } = useSWR<User[]>(
    open && memberIds.length > 0 ? `/users?ids=${memberIds.join(',')}` : null,
    () => api.get<User[]>(`/users?ids=${memberIds.join(',')}`),
  )

  // Filter to producer-or-above (the only roles that should be on the receiving
  // end of a hand-off — editors are NOT reviewers in this flow).
  const producers = React.useMemo(
    () => (users ?? []).filter((u) => isProducerOrAbove(u.role)),
    [users],
  )

  const [selectedId, setSelectedId] = React.useState<string>('')
  React.useEffect(() => {
    if (open) {
      // Auto-select the only producer if there's exactly one.
      if (producers.length === 1) setSelectedId(producers[0].id)
      else setSelectedId('')
    }
  }, [open, producers])

  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // Auto-skip if no producers exist on this project.
  React.useEffect(() => {
    if (open && users && producers.length === 0) {
      // No-op: close on next tick so the parent can wire onOpenChange.
      onOpenChange(false)
    }
  }, [open, users, producers.length, onOpenChange])

  const handleSubmit = async () => {
    if (!selectedId) return
    setSubmitting(true)
    setError(null)
    try {
      await api.patch(`/assets/${assetId}`, {
        assignee_id: selectedId,
        status: 'in_review',
      })
      onHandedOff?.(selectedId)
      onOpenChange(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Hand-off failed')
    } finally {
      setSubmitting(false)
    }
  }

  if (producers.length === 0) return null

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(92vw,420px)] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-bg-secondary p-6 shadow-2xl',
          )}
        >
          <div className="flex items-start justify-between gap-3 mb-3">
            <Dialog.Title className="text-lg font-semibold text-text-primary">
              Hand off for review?
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded-md p-1 text-text-tertiary hover:bg-bg-hover hover:text-text-primary">
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <Dialog.Description className="text-xs text-text-secondary mb-4">
            Upload complete. Pick a producer to take the next step (internal
            review). The asset's assignee moves to them and status flips to
            <em> in_review</em>.
          </Dialog.Description>

          <div className="space-y-2">
            <label className="text-xs font-medium text-text-tertiary">
              Producer
            </label>
            <select
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              className="w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-1.5 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
              disabled={submitting}
            >
              <option value="">— Select a producer</option>
              {producers.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name || u.email}
                </option>
              ))}
            </select>
          </div>

          {error && <p className="mt-3 text-xs text-status-error">{error}</p>}

          <div className="flex justify-end gap-2 mt-5">
            <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
              Keep editing
            </Button>
            <Button onClick={handleSubmit} disabled={submitting || !selectedId}>
              {submitting ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> Handing off…
                </>
              ) : (
                <>
                  <Send className="h-3.5 w-3.5 mr-1.5" /> Send for review
                </>
              )}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function isProducerOrAbove(role: UserRole): boolean {
  return role === 'producer' || role === 'admin'
}
