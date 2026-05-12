'use client'

/**
 * AssetWorkflowFields
 * -------------------
 * N1.B editable fields panel for the Fields tab on the asset detail page.
 * Distinct from the existing AssetMetadata component (which handles project-
 * level custom fields). This component is the producer's primary control
 * surface for the workflow state — phase, priority, assignee, reviewer, and
 * (for video assets) format/goal/source/style/talent.
 *
 * Every field autosaves on change via PATCH /assets/{id}. No explicit Save
 * button — the value is the truth as soon as you blur the input.
 */
import * as React from 'react'
import useSWR, { mutate as globalMutate } from 'swr'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import type {
  Asset,
  AssetPhase,
  AssetPriority,
  ProjectMember,
  User,
  VideoCustomFields,
  VideoFormat,
  VideoGoal,
  VideoSource,
  VideoStyle,
} from '@/types'

const PHASE_LABELS: Record<AssetPhase, string> = {
  internal: 'Internal review',
  client: 'Client review',
  delivered: 'Delivered',
}

const PRIORITY_LABELS: Record<AssetPriority, string> = {
  P0: 'P0 — drop everything',
  P1: 'P1 — committed this week',
  P2: 'P2 — committed this quarter',
}

const FORMAT_LABELS: Record<VideoFormat, string> = {
  'yt-long': 'YouTube long',
  shorts: 'Shorts',
  reels: 'Reels',
  tiktok: 'TikTok',
}
const GOAL_LABELS: Record<VideoGoal, string> = {
  awareness: 'Awareness',
  'lead-gen': 'Lead gen',
  education: 'Education',
  proof: 'Proof',
}
const SOURCE_LABELS: Record<VideoSource, string> = {
  'original-shoot': 'Original shoot',
  'client-supplied': 'Client supplied',
  'stock-mix': 'Stock mix',
}
const STYLE_LABELS: Record<VideoStyle, string> = {
  'talking-head': 'Talking head',
  'b-roll': 'B-roll heavy',
  'motion-graphics': 'Motion graphics',
}

interface Props {
  asset: Asset
  onUpdated?: () => void
}

export function AssetWorkflowFields({ asset, onUpdated }: Props) {
  const { data: members } = useSWR<ProjectMember[]>(
    `/projects/${asset.project_id}/members`,
    () => api.get<ProjectMember[]>(`/projects/${asset.project_id}/members`),
  )
  const { data: users } = useSWR<User[]>(
    members ? `/users?ids=${members.map((m) => m.user_id).join(',')}` : null,
    () => {
      if (!members || members.length === 0) return Promise.resolve([])
      return api.get<User[]>(`/users?ids=${members.map((m) => m.user_id).join(',')}`)
    },
  )

  const usersById = React.useMemo(() => {
    const map: Record<string, User> = {}
    for (const u of users ?? []) map[u.id] = u
    return map
  }, [users])

  // Local copy of the asset for optimistic UI. The parent's ``asset`` prop is
  // not held in SWR — it lives in ReviewProvider useState — so calling
  // ``mutate('/assets/:id')`` is a no-op for refreshing the dropdowns. We
  // instead apply the patch to local state immediately and revert on error.
  //
  // When the parent re-fetches and a fresh ``asset`` prop arrives, we resync
  // local state via the id-based useEffect below. This keeps the local copy
  // honest even if the producer leaves the tab and comes back.
  const [localAsset, setLocalAsset] = React.useState(asset)
  React.useEffect(() => {
    setLocalAsset(asset)
  }, [asset.id, asset.updated_at])

  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const patchAsset = React.useCallback(
    async (body: Record<string, unknown>) => {
      // Snapshot for rollback before we touch local state.
      const before = localAsset
      setLocalAsset((prev) => ({ ...prev, ...body } as typeof prev))
      setBusy(true)
      setError(null)
      try {
        await api.patch(`/assets/${asset.id}`, body)
        // The server's response is the truth; we don't merge it here because
        // we already applied the same change locally. The id-based effect
        // above will sync when the parent re-fetches.
        onUpdated?.()
      } catch (e: unknown) {
        // Revert the optimistic update.
        setLocalAsset(before)
        const msg = e instanceof Error ? e.message : 'Failed to save'
        setError(msg)
      } finally {
        setBusy(false)
      }
    },
    [asset.id, localAsset, onUpdated],
  )

  const isVideo = localAsset.asset_type === 'video'
  const customFields: VideoCustomFields = localAsset.custom_fields ?? {}

  return (
    <div className="space-y-5">
      {/* ── Universal workflow fields ──────────────────────────────────────── */}
      <Section title="Workflow">
        {/* Phase */}
        <Row label="Phase">
          <Select
            value={localAsset.phase}
            onChange={(v) => patchAsset({ phase: v })}
            disabled={busy || localAsset.phase === 'delivered'}
            options={[
              { value: 'internal', label: PHASE_LABELS.internal },
              { value: 'client', label: PHASE_LABELS.client },
              { value: 'delivered', label: PHASE_LABELS.delivered },
            ]}
          />
        </Row>

        {/* Priority */}
        <Row label="Priority">
          <Select
            value={localAsset.priority ?? ''}
            onChange={(v) => patchAsset({ priority: v || null })}
            disabled={busy}
            options={[
              { value: '', label: '—' },
              { value: 'P0', label: PRIORITY_LABELS.P0 },
              { value: 'P1', label: PRIORITY_LABELS.P1 },
              { value: 'P2', label: PRIORITY_LABELS.P2 },
            ]}
          />
        </Row>

        {/* Assignee */}
        <Row label="Assignee">
          <MemberSelect
            members={members ?? []}
            users={usersById}
            value={localAsset.assignee_id}
            onChange={(uid) => patchAsset({ assignee_id: uid })}
            disabled={busy}
          />
        </Row>

        {/* Reviewer */}
        <Row label="Reviewer">
          <MemberSelect
            members={members ?? []}
            users={usersById}
            value={localAsset.reviewer_id}
            onChange={(uid) => patchAsset({ reviewer_id: uid })}
            disabled={busy}
          />
        </Row>

        {/* Due / target publish date */}
        <Row label="Target date">
          <input
            type="date"
            className={inputClass}
            disabled={busy}
            defaultValue={(localAsset.due_date ?? '').split('T')[0]}
            onBlur={(e) => {
              const v = e.target.value
              const next = v ? new Date(v).toISOString() : null
              patchAsset({ due_date: next })
            }}
          />
        </Row>

        {/* Block reason — small text input */}
        <Row label="Block reason">
          <input
            type="text"
            className={inputClass}
            placeholder="(none)"
            disabled={busy}
            defaultValue={localAsset.block_reason ?? ''}
            onBlur={(e) => patchAsset({ block_reason: e.target.value || null })}
          />
        </Row>
      </Section>

      {/* ── Video-specific fields (only when asset_type === 'video') ────────── */}
      {isVideo && (
        <Section title="Video">
          <Row label="Format">
            <Select
              value={customFields.format ?? ''}
              onChange={(v) => patchAsset({ custom_fields: { ...customFields, format: v || null } })}
              disabled={busy}
              options={[
                { value: '', label: '—' },
                ...Object.entries(FORMAT_LABELS).map(([v, l]) => ({ value: v, label: l })),
              ]}
            />
          </Row>
          <Row label="Goal">
            <Select
              value={customFields.goal ?? ''}
              onChange={(v) => patchAsset({ custom_fields: { ...customFields, goal: v || null } })}
              disabled={busy}
              options={[
                { value: '', label: '—' },
                ...Object.entries(GOAL_LABELS).map(([v, l]) => ({ value: v, label: l })),
              ]}
            />
          </Row>
          <Row label="Source">
            <Select
              value={customFields.source ?? ''}
              onChange={(v) => patchAsset({ custom_fields: { ...customFields, source: v || null } })}
              disabled={busy}
              options={[
                { value: '', label: '—' },
                ...Object.entries(SOURCE_LABELS).map(([v, l]) => ({ value: v, label: l })),
              ]}
            />
          </Row>
          <Row label="Style">
            <MultiSelect
              value={customFields.style ?? []}
              onChange={(v) => patchAsset({ custom_fields: { ...customFields, style: v } })}
              disabled={busy}
              options={Object.entries(STYLE_LABELS).map(([v, l]) => ({ value: v as VideoStyle, label: l }))}
            />
          </Row>
          <Row label="Talent">
            <input
              type="text"
              className={inputClass}
              placeholder="daniel,ella"
              disabled={busy}
              defaultValue={customFields.talent ?? ''}
              onBlur={(e) => patchAsset({ custom_fields: { ...customFields, talent: e.target.value || null } })}
            />
          </Row>
        </Section>
      )}

      {error && <p className="text-xs text-status-error">Error: {error}</p>}
    </div>
  )
}

// ── Small UI primitives ─────────────────────────────────────────────────────

const inputClass =
  'w-full rounded-md border border-border bg-bg-secondary px-2 py-1 text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-[var(--accent)] disabled:opacity-60'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h4 className="text-[11px] font-semibold uppercase tracking-wider text-text-tertiary">
        {title}
      </h4>
      <div className="space-y-2">{children}</div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[100px_1fr] items-center gap-2">
      <span className="text-[11px] text-text-tertiary">{label}</span>
      <div>{children}</div>
    </div>
  )
}

function Select({
  value,
  onChange,
  options,
  disabled,
}: {
  value: string
  onChange: (v: string) => void
  options: Array<{ value: string; label: string }>
  disabled?: boolean
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

function MemberSelect({
  members,
  users,
  value,
  onChange,
  disabled,
}: {
  members: ProjectMember[]
  users: Record<string, User>
  value: string | null
  onChange: (uid: string | null) => void
  disabled?: boolean
}) {
  return (
    <select
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value || null)}
      className={inputClass}
    >
      <option value="">— Unassigned</option>
      {members.map((m) => {
        const u = users[m.user_id]
        return (
          <option key={m.user_id} value={m.user_id}>
            {u ? u.name || u.email : m.user_id}
          </option>
        )
      })}
    </select>
  )
}

function MultiSelect<T extends string>({
  value,
  onChange,
  options,
  disabled,
}: {
  value: T[]
  onChange: (v: T[]) => void
  options: Array<{ value: T; label: string }>
  disabled?: boolean
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {options.map((o) => {
        const selected = value.includes(o.value)
        return (
          <button
            key={o.value}
            type="button"
            disabled={disabled}
            onClick={() => {
              const next = selected ? value.filter((v) => v !== o.value) : [...value, o.value]
              onChange(next)
            }}
            className={cn(
              'rounded-full px-2 py-0.5 text-[11px] border transition-colors',
              selected
                ? 'bg-[var(--accent)] text-white border-[var(--accent)]'
                : 'bg-bg-secondary border-border text-text-secondary hover:bg-bg-hover',
              disabled && 'opacity-60',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
