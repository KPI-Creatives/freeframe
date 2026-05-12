'use client'

/**
 * Send-to-Client modal — the single atomic action used by producers to
 * transition an asset from internal review to client review. One click does:
 *
 *   1. flip Asset.phase to 'client'
 *   2. snapshot the current latest version as client_baseline_version_id
 *   3. mint a ShareLink with the chosen permission and optional expiry
 *   4. queue a Resend email to the client
 *   5. hand the producer back the share URL with a Copy button for fallback
 *
 * Visible only to producer+ users (the backend gate enforces this regardless).
 */
import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, Send, Copy, Check, Loader2 } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface Props {
  assetId: string
  assetName: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess?: () => void
}

type Permission = 'view' | 'comment' | 'approve'

interface SendResponse {
  asset_id: string
  phase: string
  phase_client_at: string | null
  client_baseline_version_id: string | null
  share_link_id: string
  share_url: string
}

export function SendToClientDialog({
  assetId,
  assetName,
  open,
  onOpenChange,
  onSuccess,
}: Props) {
  const [recipientEmail, setRecipientEmail] = React.useState('')
  const [permission, setPermission] = React.useState<Permission>('comment')
  const [expiresInDays, setExpiresInDays] = React.useState<number | ''>('')
  const [password, setPassword] = React.useState('')
  const [message, setMessage] = React.useState('')

  const [submitting, setSubmitting] = React.useState(false)
  const [shareUrl, setShareUrl] = React.useState<string | null>(null)
  const [copied, setCopied] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // Reset on open
  React.useEffect(() => {
    if (open) {
      setRecipientEmail('')
      setPermission('comment')
      setExpiresInDays('')
      setPassword('')
      setMessage('')
      setShareUrl(null)
      setCopied(false)
      setError(null)
    }
  }, [open])

  const handleSubmit = async () => {
    if (!recipientEmail.trim()) {
      setError('Recipient email is required')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const res = await api.post<SendResponse>(`/assets/${assetId}/send-to-client`, {
        recipient_email: recipientEmail.trim(),
        permission,
        expires_in_days: typeof expiresInDays === 'number' ? expiresInDays : null,
        password: password.trim() || null,
        message: message.trim() || null,
      })
      setShareUrl(res.share_url)
      onSuccess?.()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Send failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handleCopy = () => {
    if (!shareUrl) return
    navigator.clipboard.writeText(shareUrl)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-bg-secondary p-6 shadow-2xl',
          )}
        >
          <div className="flex items-start justify-between gap-3 mb-4">
            <div>
              <Dialog.Title className="text-lg font-semibold text-text-primary">
                Send to client review
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

          {shareUrl ? (
            // ── Success view ───────────────────────────────────────────────
            <div className="space-y-4">
              <div className="rounded-md bg-status-success/10 border border-status-success/30 p-3">
                <p className="text-sm text-status-success font-medium">
                  ✓ Sent to {recipientEmail}
                </p>
                <p className="text-xs text-text-secondary mt-1">
                  Asset moved to client phase. Email queued. Share link ready
                  for any fallback channel.
                </p>
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-text-tertiary">
                  Share URL
                </label>
                <div className="flex items-center gap-2">
                  <Input
                    value={shareUrl}
                    readOnly
                    onClick={(e) => (e.target as HTMLInputElement).select()}
                    className="text-xs font-mono"
                  />
                  <Button onClick={handleCopy} size="sm" variant="secondary">
                    {copied ? (
                      <>
                        <Check className="h-3.5 w-3.5 mr-1" /> Copied
                      </>
                    ) : (
                      <>
                        <Copy className="h-3.5 w-3.5 mr-1" /> Copy
                      </>
                    )}
                  </Button>
                </div>
              </div>
              <div className="flex justify-end">
                <Button onClick={() => onOpenChange(false)}>Done</Button>
              </div>
            </div>
          ) : (
            // ── Form ───────────────────────────────────────────────────────
            <div className="space-y-3">
              <Field label="Recipient email" required>
                <Input
                  type="email"
                  value={recipientEmail}
                  onChange={(e) => setRecipientEmail(e.target.value)}
                  placeholder="jane@aduscale.com"
                  disabled={submitting}
                />
              </Field>

              <Field label="Permission">
                <div className="flex gap-1">
                  {(['view', 'comment', 'approve'] as Permission[]).map((p) => (
                    <button
                      key={p}
                      type="button"
                      disabled={submitting}
                      onClick={() => setPermission(p)}
                      className={cn(
                        'flex-1 rounded-md border px-3 py-1.5 text-xs capitalize transition-colors',
                        permission === p
                          ? 'bg-[var(--accent)] text-white border-[var(--accent)]'
                          : 'bg-bg-tertiary border-border text-text-secondary hover:bg-bg-hover',
                      )}
                    >
                      {p === 'view' ? 'View only' : p === 'comment' ? 'Comment' : 'Approve'}
                    </button>
                  ))}
                </div>
              </Field>

              <div className="grid grid-cols-2 gap-3">
                <Field label="Expires in (days)">
                  <Input
                    type="number"
                    min={1}
                    max={365}
                    value={expiresInDays}
                    onChange={(e) =>
                      setExpiresInDays(e.target.value ? Number(e.target.value) : '')
                    }
                    placeholder="never"
                    disabled={submitting}
                  />
                </Field>
                <Field label="Password (optional)">
                  <Input
                    type="text"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="—"
                    disabled={submitting}
                  />
                </Field>
              </div>

              <Field label="Custom message (optional)">
                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={3}
                  placeholder="Hi Jane, the cut for review — please share thoughts by Friday."
                  disabled={submitting}
                  className="w-full rounded-md border border-border bg-bg-tertiary px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-[var(--accent)] disabled:opacity-60"
                />
              </Field>

              {error && <p className="text-xs text-status-error">{error}</p>}

              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={submitting}>
                  Cancel
                </Button>
                <Button onClick={handleSubmit} disabled={submitting || !recipientEmail.trim()}>
                  {submitting ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" /> Sending…
                    </>
                  ) : (
                    <>
                      <Send className="h-3.5 w-3.5 mr-1.5" /> Send
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

function Field({
  label,
  required,
  children,
}: {
  label: string
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-text-secondary">
        {label}
        {required && <span className="text-status-error"> *</span>}
      </label>
      {children}
    </div>
  )
}
