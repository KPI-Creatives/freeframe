'use client'

/**
 * VersionCompare — side-by-side dual video player.
 *
 * Producer-driven UX: when reviewing v3 (the proposed final), it's useful to
 * see v2 (or v1) at the same time to verify a fix actually landed without
 * having to scrub back and forth in a single player. This component renders
 * two players in a horizontal split with independent version pickers — left
 * player defaults to currentVersion, right player defaults to the prior
 * ready version.
 *
 * Scope: NO scrub sync in v1. The producer scrubs each side manually. Adding
 * a shared scrub head is non-trivial because timecodes between versions can
 * differ (edited cuts shift markers). Defer to v2 if real users ask for it.
 *
 * Mobile: stacks vertically below ~768px. Compare on a phone is a degraded
 * experience anyway; producers use this on desktop.
 */
import * as React from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import { X, ChevronDown, AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { VideoPlayer } from './video-player'
import type { AssetVersion, AssetVersionStatus } from '@/types'

interface Props {
  assetId: string
  versions: AssetVersion[]
  primaryVersionId: string  // the version showing in the main viewer; left side default
  onClose: () => void
}

const versionStatusConfig: Record<
  AssetVersionStatus,
  { label: string; className: string; icon: React.ReactNode }
> = {
  uploading: { label: 'Uploading', className: 'text-status-info', icon: <Loader2 className="h-2.5 w-2.5 animate-spin" /> },
  processing: { label: 'Processing', className: 'text-status-warning', icon: <Loader2 className="h-2.5 w-2.5 animate-spin" /> },
  ready: { label: 'Ready', className: 'text-status-success', icon: <CheckCircle2 className="h-2.5 w-2.5" /> },
  failed: { label: 'Failed', className: 'text-status-error', icon: <AlertCircle className="h-2.5 w-2.5" /> },
}

export function VersionCompare({ assetId, versions, primaryVersionId, onClose }: Props) {
  const sorted = React.useMemo(
    () => [...versions].sort((a, b) => a.version_number - b.version_number),
    [versions],
  )

  // Default selections:
  //   left  = whatever is current in the main viewer (the proposed new cut)
  //   right = the previous ready version (the prior cut to compare against)
  const defaultLeft = sorted.find((v) => v.id === primaryVersionId) ?? sorted[sorted.length - 1]
  const priorReady = [...sorted].reverse().find(
    (v) => v.id !== defaultLeft.id && v.processing_status === 'ready',
  )
  const defaultRight = priorReady ?? sorted[0]

  const [leftVersion, setLeftVersion] = React.useState<AssetVersion>(defaultLeft)
  const [rightVersion, setRightVersion] = React.useState<AssetVersion>(defaultRight)

  return (
    <div className="absolute inset-0 z-30 flex flex-col bg-bg-primary">
      {/* Compare header */}
      <div className="shrink-0 flex items-center justify-between px-3 h-10 border-b border-border bg-bg-secondary">
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span className="text-text-primary font-medium">Compare</span>
          <span className="text-text-tertiary">— two versions side-by-side, scrub each independently</span>
        </div>
        <button
          onClick={onClose}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
          title="Close compare"
        >
          <X className="h-3.5 w-3.5" />
          Exit compare
        </button>
      </div>

      {/* Dual player split — horizontal on >=md, stacked under */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0">
        <CompareSide
          label="Left"
          assetId={assetId}
          versions={sorted}
          selected={leftVersion}
          onSelect={setLeftVersion}
          className="md:border-r border-border"
        />
        <CompareSide
          label="Right"
          assetId={assetId}
          versions={sorted}
          selected={rightVersion}
          onSelect={setRightVersion}
          className="md:border-l-0 border-t md:border-t-0 border-border"
        />
      </div>
    </div>
  )
}

interface SideProps {
  label: string
  assetId: string
  versions: AssetVersion[]
  selected: AssetVersion
  onSelect: (v: AssetVersion) => void
  className?: string
}

function CompareSide({ label, assetId, versions, selected, onSelect, className }: SideProps) {
  return (
    <div className={cn('flex-1 flex flex-col min-h-0 min-w-0', className)}>
      {/* Side header — version picker */}
      <div className="shrink-0 flex items-center justify-between px-3 h-9 border-b border-border bg-bg-secondary/50">
        <span className="text-[11px] uppercase tracking-wider text-text-tertiary">{label}</span>
        <DropdownMenu.Root>
          <DropdownMenu.Trigger asChild>
            <button className="inline-flex items-center gap-1 rounded-md px-2 py-1 bg-accent text-white text-xs font-medium hover:bg-accent/90 transition-colors outline-none">
              v{selected.version_number}
              <ChevronDown className="h-3 w-3 opacity-70" />
            </button>
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content
              align="end"
              sideOffset={6}
              className="z-[200] min-w-[160px] rounded-xl border border-border bg-bg-elevated shadow-2xl py-1.5"
            >
              {versions.map((v) => {
                const isActive = v.id === selected.id
                const cfg = versionStatusConfig[v.processing_status]
                const disabled = v.processing_status === 'uploading' || v.processing_status === 'processing'
                return (
                  <DropdownMenu.Item
                    key={v.id}
                    disabled={disabled}
                    onSelect={() => onSelect(v)}
                    className={cn(
                      'flex items-center justify-between gap-3 mx-1 px-2.5 py-2 rounded-lg text-sm cursor-pointer outline-none transition-colors',
                      isActive
                        ? 'bg-accent/10 text-accent font-medium'
                        : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary',
                      disabled && 'opacity-50 cursor-not-allowed',
                    )}
                  >
                    <span>v{v.version_number}</span>
                    <span className={cn('inline-flex items-center gap-1 text-[11px]', cfg.className)}>
                      {cfg.icon}
                      {cfg.label}
                    </span>
                  </DropdownMenu.Item>
                )
              })}
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>

      {/* Player. Keying on version.id forces a fresh mount when version
          changes, which avoids hls.js trying to switch streams in place
          (which it can do but is flaky on cross-version segment maps). */}
      <div className="flex-1 min-h-0">
        <CompareVideoFrame
          key={selected.id}
          assetId={assetId}
          versionId={selected.id}
        />
      </div>
    </div>
  )
}

/** Thin wrapper that pins the VideoPlayer to a specific version. We pass
    the version through initialStreamUrl by querying the share-less
    /assets/:id/stream endpoint directly. */
function CompareVideoFrame({ assetId, versionId }: { assetId: string; versionId: string }) {
  // Use VideoPlayer with currentVersion overridden via the review store —
  // but the store is global. Instead we let VideoPlayer fetch by itself using
  // the version_id baked into a synthetic initialStreamUrl. Cleaner approach:
  // pre-fetch the URL here, then pass it as initialStreamUrl.
  const [streamUrl, setStreamUrl] = React.useState<string | null>(null)

  React.useEffect(() => {
    let cancelled = false
    setStreamUrl(null)
    import('@/lib/api').then(({ api }) => {
      if (cancelled) return
      api
        .get<{ url: string }>(`/assets/${assetId}/stream?version_id=${versionId}`)
        .then((data) => {
          if (cancelled) return
          setStreamUrl(data.url)
        })
        .catch(() => {})
    })
    return () => {
      cancelled = true
    }
  }, [assetId, versionId])

  if (!streamUrl) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-text-tertiary">
        Loading version stream…
      </div>
    )
  }

  return (
    <VideoPlayer
      assetId={assetId}
      initialStreamUrl={streamUrl}
      className="h-full"
    />
  )
}
