'use client'

/**
 * Send-to-Client modal — single atomic action used by producers to transition
 * an asset from internal review to client review. One submit does:
 *
 *   1. flip Asset.phase to 'client'
 *   2. snapshot the current latest version as client_baseline_version_id
 *   3. mint a ShareLink with the chosen permission and optional expiry
 *   4. queue a Resend email to the client
 *   5. hand the producer back the share URL with a Copy button for fallback
 *
 * UX principles after the first feedback round:
 *
 *   * Success state takes over the WHOLE modal — big check, big share URL,
 *     Copy button and an Open-in-new-tab button. Impossible to miss.
 *   * The share-link is the durable artifact. The email is best-effort —
 *     copy the URL even if you suspect the email got eaten by spam.
 *   * The browser console gets a log line on every submit (response + url).
 *   * Errors render as a red banner inside the dialog, NOT a silent failure.
 */
import * as React from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X, Send, Copy, Check, Loader2, ExternalLink, CheckCircle2 } from 'lucide-react'
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
  const [result, setResult] = React.useState<SendResponse | null>(null)
  const [copied, setCopied] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  // Reset when the modal opens.
  React.useEffect(() => {
    if (open) {
      setRecipientEmail('')
      setPermission('comment')
      setExpiresInDays('')
      setPassword('')
      setMessage('')
      setResult(null)
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
      const payload = {
        recipient_email: recipientEmail.trim(),
        permission,
        expires_in_days: typeof expiresInDays === 'number' ? expiresInDays : null,
        password: password.trim() || null,
        message: message.trim() || null,
      }
      // eslint-disable-next-line no-console
      console.log('[send-to-client] POST payload:', payload)
      const res = await api.post<SendResponse>(`/assets/${assetId}/send-to-client`, payload)
      // eslint-disable-next-line no-console
      console.log('[send-to-client] response:', res)
      if (!res || !res.share_url) {
        throw new Error(
          `Unexpected response shape — got ${JSON.stringify(res).slice(0, 200)}`,
        )
      }
      setResult(res)
      onSuccess?.()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Send failed'
      // eslint-disable-next-line no-console
      console.error('[send-to-client] error:', e)
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  const handleCopy = () => {
    if (!result?.share_url) return
    navigator.clipboard.writeText(result.share_url)
    setCopied(true)
    setTimeout(() => setCopied(false), 2500)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60 z-50" />
        <Dialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 w-[min(94vw,560px)] -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border bg-bg-secondary p-6 shadow-2xl',
          )}
        >
          {/* Header is suppressed in the success state — the big banner IS the header. */}
          {!result && (
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
          )}

          {result ? (
            // ── Success view — takes over the whole modal ─────────────────
            <div className="space-y-5">
              <div className="flex flex-col items-center text-center gap-2 py-2">
                <div className="rounded-full bg-status-success/15 p-3">
                  <CheckCircle2 className="h-8 w-8 text-status-success" />
                </div>
                <Dialog.Title className="text-lg font-semibold text-text-primary">
                  Sent to client review
                </Dialog.Title>
                <Dialog.Description className="text-sm text-text-secondary">
                  Asset moved to <strong className="text-text-primary">client phase</strong>.
                  Email queued to <strong className="text-text-primary">{recipientEmail}</strong>.
                </Dialog.Description>
                <p className="text-xs text-text-tertiary mt-2 max-w-[400px]">
                  If the email doesn't arrive within a few minutes, copy the
                  share URL below and send it via your preferred channel —
                  the link is already active.
                </p>
              </div>

              <div className="space-y-1.5">
                <label className="text-xs font-medium text-text-tertiary">
                  Share URL
                </label>
                <div className="flex items-center gap-2">
                  <Input
                    value={result.share_url}
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
                  <a
                    href={result.share_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-center justify-center h-9 w-9 rounded-md border border-border bg-bg-tertiary text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors"
                    title="Open in a new tab (preview)"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </div>
              </div>

              <div className="flex justify-between items-center pt-2">
                <button
                  className="text-xs text-text-tertiary hover:text-text-secondary underline underline-offset-2"
                  onClick={() => {
                    // Send to another email — reset to form state, keep modal open.
                    setResult(null)
                    setRecipientEmail('')
                    setError(null)
                  }}
                >
                  Send to another email
                </button>
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

              {error && (
                <div className="rounded-md border border-status-error/40 bg-status-error/10 px-3 py-2">
                  <p className="text-xs text-status-error font-medium">
                    Send failed
                  </p>
                  <p className="text-[11px] text-status-error/80 mt-0.5">{error}</p>
                </div>
              )}

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
