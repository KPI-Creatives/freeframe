'use client'

/**
 * Settings → API Tokens
 *
 * Personal API tokens (ft_…) for scripted Frame access. Surface mirrors
 * GitHub PATs / Stripe API keys: list existing, mint a new one (shown
 * ONCE), revoke when retired.
 *
 * Producer/admin only — backend enforces this on POST; the UI just shows
 * a fallback message to editors who navigate here. We don't fully hide
 * the page because the editor may still want to view tokens they had
 * created when they were a producer.
 */

import * as React from 'react'
import { Key, Plus, Copy, Trash2, X, Check } from 'lucide-react'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface ApiTokenListItem {
  id: string
  name: string
  prefix: string
  last_used_at: string | null
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}

interface ApiTokenCreateResp extends ApiTokenListItem {
  token: string
}

const EXPIRY_OPTIONS = [
  { label: '7 days',  days: 7 },
  { label: '30 days', days: 30 },
  { label: '90 days', days: 90 },
  { label: 'Never',   days: null as number | null },
]

export default function ApiTokensPage() {
  const { user } = useAuthStore()
  const canCreate = user?.role === 'producer' || user?.role === 'admin'

  const [tokens, setTokens] = React.useState<ApiTokenListItem[] | null>(null)
  const [loadError, setLoadError] = React.useState<string | null>(null)

  const [showCreate, setShowCreate] = React.useState(false)
  const [newName, setNewName] = React.useState('')
  const [newExpiryDays, setNewExpiryDays] = React.useState<number | null>(null)
  const [creating, setCreating] = React.useState(false)
  const [createError, setCreateError] = React.useState<string | null>(null)
  // Plain token returned by the API exactly once. Held only in memory and
  // dropped when the user dismisses the success modal. NEVER persisted.
  const [justMintedToken, setJustMintedToken] = React.useState<string | null>(null)
  const [copied, setCopied] = React.useState(false)

  const fetchTokens = React.useCallback(async () => {
    try {
      const list = await api.get<ApiTokenListItem[]>('/me/api-tokens')
      setTokens(list)
      setLoadError(null)
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load tokens')
    }
  }, [])

  React.useEffect(() => {
    fetchTokens()
  }, [fetchTokens])

  async function handleCreate() {
    setCreateError(null)
    if (!newName.trim()) {
      setCreateError('Name is required')
      return
    }
    setCreating(true)
    try {
      const body: Record<string, unknown> = { name: newName.trim() }
      if (newExpiryDays != null) {
        const d = new Date()
        d.setDate(d.getDate() + newExpiryDays)
        body.expires_at = d.toISOString()
      }
      const resp = await api.post<ApiTokenCreateResp>('/me/api-tokens', body)
      setJustMintedToken(resp.token)
      setNewName('')
      setNewExpiryDays(null)
      setShowCreate(false)
      await fetchTokens()
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create token')
    } finally {
      setCreating(false)
    }
  }

  async function handleRevoke(id: string) {
    if (!confirm('Revoke this token? Any scripts using it will stop authenticating immediately.')) return
    try {
      await api.delete(`/me/api-tokens/${id}`)
      await fetchTokens()
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Failed to revoke')
    }
  }

  const copyToken = async () => {
    if (!justMintedToken) return
    try {
      await navigator.clipboard.writeText(justMintedToken)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API may be blocked; user can still select + copy manually
    }
  }

  return (
    <div className="p-6 max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent-muted">
          <Key className="h-5 w-5 text-accent" />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-text-primary">API Tokens</h1>
          <p className="text-sm text-text-secondary">
            Personal tokens for scripts, automation, and integrations. Each token
            authenticates as you.
          </p>
        </div>
      </div>

      {/* Create token CTA */}
      {canCreate ? (
        <div className="flex items-center justify-end">
          <Button onClick={() => setShowCreate(true)} className="gap-1.5">
            <Plus className="h-4 w-4" />
            New token
          </Button>
        </div>
      ) : (
        <div className="rounded-md bg-bg-secondary border border-border px-4 py-3 text-xs text-text-secondary">
          Creating API tokens requires <strong>producer</strong> or <strong>admin</strong> role.
        </div>
      )}

      {/* List */}
      {loadError && (
        <p className="text-xs text-status-error">Error loading tokens: {loadError}</p>
      )}

      {tokens === null ? (
        <p className="text-xs text-text-tertiary">Loading…</p>
      ) : tokens.length === 0 ? (
        <p className="text-xs text-text-tertiary">No tokens yet.</p>
      ) : (
        <ul className="space-y-2">
          {tokens.map((t) => {
            const isRevoked = !!t.revoked_at
            const isExpired = t.expires_at ? new Date(t.expires_at) < new Date() : false
            return (
              <li
                key={t.id}
                className="rounded-lg border border-border bg-bg-secondary/50 px-4 py-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-text-primary truncate">{t.name}</span>
                      {(isRevoked || isExpired) && (
                        <span className="text-[10px] uppercase tracking-wide text-status-error">
                          {isRevoked ? 'revoked' : 'expired'}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-text-tertiary">
                      <span className="font-mono">ft_{t.prefix}_…</span>
                      <span>created {new Date(t.created_at).toLocaleDateString()}</span>
                      {t.last_used_at && (
                        <span>last used {new Date(t.last_used_at).toLocaleString()}</span>
                      )}
                      {t.expires_at && !isExpired && (
                        <span>expires {new Date(t.expires_at).toLocaleDateString()}</span>
                      )}
                    </div>
                  </div>
                  {!isRevoked && (
                    <button
                      onClick={() => handleRevoke(t.id)}
                      className="text-text-tertiary hover:text-status-error p-1 rounded-md hover:bg-bg-hover transition-colors"
                      aria-label="Revoke"
                      title="Revoke token"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-xl border border-border bg-bg-elevated shadow-2xl p-5">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-text-primary">Create API token</h2>
              <button
                onClick={() => { setShowCreate(false); setCreateError(null) }}
                className="text-text-tertiary hover:text-text-primary p-1 rounded-md hover:bg-bg-hover transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-4">
              <div>
                <label className="text-xs text-text-secondary mb-1 block">Name</label>
                <Input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="kpi-migrator, kpi-sync, cli-yk"
                  autoFocus
                />
              </div>
              <div>
                <label className="text-xs text-text-secondary mb-1 block">Expires</label>
                <div className="flex gap-1.5 flex-wrap">
                  {EXPIRY_OPTIONS.map((opt) => (
                    <button
                      key={opt.label}
                      type="button"
                      onClick={() => setNewExpiryDays(opt.days)}
                      className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                        newExpiryDays === opt.days
                          ? 'border-[var(--accent)] bg-[var(--accent)]/10 text-text-primary'
                          : 'border-border text-text-secondary hover:bg-bg-hover'
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>
              {createError && <p className="text-xs text-status-error">{createError}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="ghost" onClick={() => { setShowCreate(false); setCreateError(null) }}>
                  Cancel
                </Button>
                <Button onClick={handleCreate} disabled={creating}>
                  {creating ? 'Creating…' : 'Create token'}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* One-time secret reveal */}
      {justMintedToken && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-xl border border-border bg-bg-elevated shadow-2xl p-5">
            <h2 className="text-sm font-semibold text-text-primary mb-1">
              Your new token
            </h2>
            <p className="text-xs text-text-secondary mb-4">
              Copy it now — this is the <strong>only</strong> time it's shown.
              Store it in your password manager or directly in the script's env.
            </p>
            <div className="flex items-center gap-2 rounded-md border border-border bg-bg-primary p-2.5 font-mono text-[11px] text-text-primary break-all">
              <span className="flex-1">{justMintedToken}</span>
              <button
                onClick={copyToken}
                className="shrink-0 inline-flex items-center gap-1 rounded-md bg-[var(--accent)] text-white text-xs px-2.5 py-1 hover:opacity-90"
              >
                {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                {copied ? 'Copied' : 'Copy'}
              </button>
            </div>
            <p className="text-[11px] text-text-tertiary mt-3">
              Authenticate with <code className="text-text-secondary">Authorization: Bearer &lt;token&gt;</code>.
            </p>
            <div className="flex justify-end mt-5">
              <Button onClick={() => setJustMintedToken(null)}>I&apos;ve saved it</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
