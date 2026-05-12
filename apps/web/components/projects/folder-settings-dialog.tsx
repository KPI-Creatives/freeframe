'use client'

/**
 * FolderSettingsDialog — per-folder configuration. Today this is one field
 * (`time_tracking_default`), but the dialog is scoped to "settings" so future
 * additions (e.g. default phase, asset-naming convention) land here without
 * dialog proliferation.
 *
 * Mounted from the folder right-click context menu (folder-tree.tsx). Closes
 * on save / cancel / Escape.
 */

import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { Clock, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { mutate as globalMutate } from 'swr'
import type { Folder, FolderTreeNode, TimeTrackingDefault } from '@/types'

interface FolderSettings {
  id: string
  name: string
  time_tracking_default: TimeTrackingDefault
  /** Optional — only present if the dialog was opened from a tree node where
   *  the resolved value is known. Used to show "Inherit (currently: on)". */
  time_tracking_resolved?: boolean
  projectId: string
}

interface Props {
  open: boolean
  onClose: () => void
  folder: FolderSettings | null
}

export function FolderSettingsDialog({ open, onClose, folder }: Props) {
  const [value, setValue] = React.useState<TimeTrackingDefault>(
    folder?.time_tracking_default ?? 'inherit',
  )
  const [submitting, setSubmitting] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [resolved, setResolved] = React.useState<boolean | null>(
    folder?.time_tracking_resolved ?? null,
  )

  // Resync when the dialog is opened on a different folder.
  React.useEffect(() => {
    if (folder) {
      setValue(folder.time_tracking_default)
      setResolved(folder.time_tracking_resolved ?? null)
      setError(null)
    }
  }, [folder?.id])

  // When the dialog is open and the user toggled to "inherit", fetch the
  // current effective value so we can show "Inherit (currently: on)". The
  // tree-node value is cached at render time; the user may have just
  // changed the parent's policy. One small GET — cheap.
  React.useEffect(() => {
    if (!open || !folder) return
    if (value !== 'inherit') return
    let cancelled = false
    api
      .get<Folder>(`/folders/${folder.id}`)
      .then((f) => {
        if (!cancelled) setResolved(f.time_tracking_resolved)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [open, folder?.id, value])

  if (!folder) return null

  const handleSave = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await api.patch(`/folders/${folder.id}`, { time_tracking_default: value })
      // Refresh the folder tree so the right-click menu reflects the new
      // policy if the user opens it again.
      globalMutate(`/projects/${folder.projectId}/folder-tree`).catch(() => {})
      onClose()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  const inheritLabel =
    resolved === null
      ? 'Inherit from parent'
      : `Inherit from parent (currently: ${resolved ? 'on' : 'off'})`

  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (!v) onClose() }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(460px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-bg-elevated shadow-2xl p-5 focus:outline-none">
          <div className="flex items-start justify-between mb-1">
            <Dialog.Title className="text-sm font-semibold text-text-primary">
              Folder settings
            </Dialog.Title>
            <button
              onClick={onClose}
              className="text-text-tertiary hover:text-text-primary p-1 rounded-md hover:bg-bg-hover transition-colors"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <Dialog.Description className="text-xs text-text-secondary mb-5">
            {folder.name}
          </Dialog.Description>

          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Clock className="h-3.5 w-3.5 text-text-secondary" />
              <span className="text-xs font-medium text-text-primary">
                Track editing time on assets in this folder
              </span>
            </div>

            <RadioRow
              checked={value === 'on'}
              onClick={() => setValue('on')}
              label="On"
              description="Every new upload here triggers the time-tracking modal."
            />
            <RadioRow
              checked={value === 'off'}
              onClick={() => setValue('off')}
              label="Off"
              description="No modal — right for raw footage and other unmonitored bins."
            />
            <RadioRow
              checked={value === 'inherit'}
              onClick={() => setValue('inherit')}
              label={inheritLabel}
              description="Defer to the parent folder's setting. Root default is off."
            />

            <p className="text-[11px] text-text-tertiary leading-relaxed pt-2">
              The setting only affects <em>new</em> assets created inside this folder.
              Existing assets keep their per-asset toggle, which you can flip in
              the Fields tab on the asset detail page.
            </p>
          </div>

          {error && <p className="mt-3 text-xs text-status-error">{error}</p>}

          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={onClose}
              disabled={submitting}
              className="rounded-md px-3 py-1.5 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={submitting}
              className="rounded-md bg-[var(--accent)] text-white text-xs font-medium px-4 py-1.5 hover:opacity-90 transition-opacity disabled:opacity-40"
            >
              {submitting ? 'Saving…' : 'Save'}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

interface RadioRowProps {
  checked: boolean
  onClick: () => void
  label: string
  description: string
}

function RadioRow({ checked, onClick, label, description }: RadioRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'w-full text-left rounded-lg border px-3 py-2.5 transition-colors',
        checked
          ? 'border-[var(--accent)] bg-[var(--accent)]/5'
          : 'border-border hover:bg-bg-hover',
      )}
    >
      <div className="flex items-center gap-2 mb-0.5">
        <span
          className={cn(
            'inline-block h-3.5 w-3.5 rounded-full border-2 shrink-0',
            checked
              ? 'border-[var(--accent)] bg-[var(--accent)]'
              : 'border-border bg-transparent',
          )}
        />
        <span className="text-xs text-text-primary font-medium">{label}</span>
      </div>
      <p className="text-[11px] text-text-tertiary pl-5 leading-relaxed">
        {description}
      </p>
    </button>
  )
}
