'use client'

/**
 * TimeSpentSection — list per-version minutes_spent + total for an asset.
 *
 * Rendered inside AssetWorkflowFields (the "Fields" tab) when
 * `asset.track_time === true`. Each row is clickable: click → re-opens the
 * TimeTrackingModal in edit mode (seeded with the current value), so the
 * editor can correct a typo or the producer can fill in a Skip.
 *
 * Total is computed locally from the version list rather than read from
 * `asset.total_minutes_spent` because the version list arrives via a
 * separate fetch and may be fresher than the asset's denormalised counter
 * in fast-edit sequences. The two should converge after one render cycle.
 */

import * as React from 'react'
import useSWR from 'swr'
import { Pencil } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatMinutes } from './time-tracking-modal'
import { useTimeTrackingStore } from '@/stores/time-tracking-store'
import type { Asset, AssetVersion } from '@/types'

interface Props {
  asset: Asset
}

export function TimeSpentSection({ asset }: Props) {
  const { data: versions } = useSWR<AssetVersion[]>(
    `/assets/${asset.id}/versions`,
    () => api.get<AssetVersion[]>(`/assets/${asset.id}/versions`),
  )

  const openForEdit = useTimeTrackingStore((s) => s.openForEdit)

  if (!asset.track_time) return null

  const sortedVersions = [...(versions ?? [])].sort(
    (a, b) => a.version_number - b.version_number,
  )

  // Total computed from minutes_spent across versions. Untracked versions
  // (minutes_spent == null) contribute 0.
  const total = sortedVersions.reduce(
    (acc, v) => acc + (v.minutes_spent ?? 0),
    0,
  )

  return (
    <div className="rounded-lg border border-border bg-bg-secondary/30 px-3 py-3 space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs uppercase tracking-wider text-text-tertiary">
          Time spent
        </h3>
        <span className="text-xs text-text-secondary tabular-nums">
          Total: <span className="text-text-primary font-medium">{formatMinutes(total) || '0m'}</span>
        </span>
      </div>

      {sortedVersions.length === 0 ? (
        <p className="text-[11px] text-text-tertiary">No versions yet.</p>
      ) : (
        <ul className="space-y-1">
          {sortedVersions.map((v) => {
            const minutes = v.minutes_spent
            const isSkipped = minutes === null
            return (
              <li
                key={v.id}
                className="flex items-center justify-between gap-2 text-xs"
              >
                <span className="text-text-secondary">v{v.version_number}</span>
                <button
                  onClick={() =>
                    openForEdit({
                      assetId: asset.id,
                      versionId: v.id,
                      assetName: asset.name,
                      versionNumber: v.version_number,
                      // priorTotalMinutes = total of OTHER versions (excludes
                      // this one) so the modal's preview reads "+X to N".
                      priorTotalMinutes: total - (minutes ?? 0),
                      initialMinutes: minutes,
                    })
                  }
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-md px-2 py-1 hover:bg-bg-hover transition-colors',
                    isSkipped ? 'text-text-tertiary' : 'text-text-primary',
                  )}
                  title="Edit time entry"
                >
                  <span className="tabular-nums">
                    {isSkipped ? '— skipped' : formatMinutes(minutes) || '0m'}
                  </span>
                  <Pencil className="h-3 w-3 opacity-50" />
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
