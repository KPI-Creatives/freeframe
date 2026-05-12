'use client'

/**
 * VersionCompare — synced dual-frame video compare with per-side review tools.
 *
 * Producer-driven UX: when reviewing v3, compare against v2 (or v1) without
 * scrubbing back and forth in a single player. Both videos play in lock-step
 * — one play/pause button, one progress bar, one current-time readout — and
 * the comments left on each version are surfaced beside their own video so
 * the producer can verify "this note on v2 has been addressed in v3" without
 * leaving Compare Mode.
 *
 * Layout:
 *
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ Compare header (close, label)                                     │
 *   ├──────────┬──────────────────────────┬────────────────┬────────────┤
 *   │ LEFT     │ Left version picker      │ Right picker   │ RIGHT      │
 *   │ comments │ ┌──────────────────────┐ │ ┌────────────┐ │ comments   │
 *   │ (v2)     │ │  <video> (primary)   │ │ │ <video>    │ │ (v3)       │
 *   │ purple   │ │                      │ │ │            │ │ cyan       │
 *   │          │ └──────────────────────┘ │ └────────────┘ │            │
 *   ├──────────┴──────────────────────────┴────────────────┴────────────┤
 *   │  ▶  ─●─•─•──•───•──•──  0:34 / 8:02                                │
 *   │       ↑ purple dots above (left comments),                         │
 *   │       cyan dots below the track (right comments)                   │
 *   └──────────────────────────────────────────────────────────────────┘
 *
 * Sync rules:
 *   * Play/Pause/Seek/Rate on the PRIMARY (left) video are mirrored to the
 *     secondary in real time via direct ref calls.
 *   * Drift correction every 500ms: if abs(left - right) > 0.3s, snap
 *     secondary to primary.
 *   * Clicking any comment (marker or side-panel row) calls handleSeek(t)
 *     which seeks both videos — keeping the synced-playback contract.
 */
import * as React from 'react'
import * as DropdownMenu from '@radix-ui/react-dropdown-menu'
import {
  X,
  ChevronDown,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Play,
  Pause,
  MessageSquare,
} from 'lucide-react'
import { cn, formatTime, formatTimecode } from '@/lib/utils'
import { useVideoPlayer } from '@/hooks/use-video-player'
import { useComments, type CommentWithReplies } from '@/hooks/use-comments'
import { api } from '@/lib/api'
import type { AssetVersion, AssetVersionStatus } from '@/types'

// Per-side accent colors. Chosen to contrast cleanly against the dark theme
// and to be distinguishable for the (small fraction of) users with red-green
// CVD: purple (~280° hue) vs cyan (~190° hue) reads as two distinct hues even
// with deuteranopia. Both also stay legible on the existing #1e1e22 panel
// background. Left = primary version being inspected (purple, matches the
// existing v-badge accent); Right = comparison baseline (cyan).
const LEFT_COLOR = '#a694ff'
const RIGHT_COLOR = '#22d3ee'

interface Props {
  assetId: string
  versions: AssetVersion[]
  primaryVersionId: string
  onClose: () => void
}

const statusCfg: Record<
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

  const defaultLeft = sorted.find((v) => v.id === primaryVersionId) ?? sorted[sorted.length - 1]
  const priorReady = [...sorted].reverse().find(
    (v) => v.id !== defaultLeft.id && v.processing_status === 'ready',
  )
  const defaultRight = priorReady ?? sorted[0]

  const [leftVersion, setLeftVersion] = React.useState<AssetVersion>(defaultLeft)
  const [rightVersion, setRightVersion] = React.useState<AssetVersion>(defaultRight)

  // Stream URL pre-fetch per side (so useVideoPlayer gets a concrete URL).
  const leftUrl = useStreamUrl(assetId, leftVersion.id)
  const rightUrl = useStreamUrl(assetId, rightVersion.id)

  // Per-side video player instance — each manages its own HLS bootstrap.
  const left = useVideoPlayer(leftUrl)
  const right = useVideoPlayer(rightUrl)

  // Per-side comments. We pull them via useComments which is already
  // version-scoped, so changing the dropdown automatically swaps the panel.
  const leftCommentsRaw = useComments(assetId, leftVersion.id).comments
  const rightCommentsRaw = useComments(assetId, rightVersion.id).comments

  // Only timecoded, unresolved, top-level comments make sense on the
  // compare timeline. Replies stay attached to their parent in the side
  // panel (we don't surface them as separate rows here — clutter).
  const leftComments = React.useMemo(
    () => filterTimecoded(leftCommentsRaw),
    [leftCommentsRaw],
  )
  const rightComments = React.useMemo(
    () => filterTimecoded(rightCommentsRaw),
    [rightCommentsRaw],
  )

  // ── Sync: mirror primary -> secondary on play/pause/seek/rate ───────────
  React.useEffect(() => {
    if (left.isPlaying && !right.isPlaying) right.play()
    if (!left.isPlaying && right.isPlaying) right.pause()
  }, [left.isPlaying, right])

  React.useEffect(() => {
    right.setPlaybackRate(left.playbackRate)
  }, [left.playbackRate, right])

  React.useEffect(() => {
    const id = setInterval(() => {
      const drift = Math.abs(left.currentTime - right.currentTime)
      if (drift > 0.3) {
        right.seek(left.currentTime)
      }
    }, 500)
    return () => clearInterval(id)
  }, [left, right])

  const togglePlay = () => {
    if (left.isPlaying) {
      left.pause()
      right.pause()
    } else {
      left.play()
      right.play()
    }
  }

  const handleSeek = React.useCallback(
    (t: number) => {
      left.seek(t)
      right.seek(t)
    },
    [left, right],
  )

  // Track which comment the user just clicked, so we can flash it on its
  // own side. Resets after a short delay — purely a visual cue, doesn't
  // affect playback behavior.
  const [focusedCommentId, setFocusedCommentId] = React.useState<string | null>(null)
  React.useEffect(() => {
    if (!focusedCommentId) return
    const id = setTimeout(() => setFocusedCommentId(null), 1600)
    return () => clearTimeout(id)
  }, [focusedCommentId])

  const handleCommentClick = React.useCallback(
    (c: CommentWithReplies) => {
      if (c.timecode_start === null) return
      handleSeek(c.timecode_start)
      setFocusedCommentId(c.id)
      if (left.isPlaying) {
        left.pause()
        right.pause()
      }
    },
    [handleSeek, left, right],
  )

  const duration = Math.min(left.duration || Infinity, right.duration || Infinity)
  const safeDuration = Number.isFinite(duration) ? duration : 0

  return (
    <div className="absolute inset-0 z-30 flex flex-col bg-bg-primary">
      {/* Header */}
      <div className="shrink-0 flex items-center justify-between px-3 h-10 border-b border-border bg-bg-secondary">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-text-primary font-medium">Compare</span>
          <span className="text-text-tertiary">— synced playback, comments side-by-side</span>
        </div>
        <button
          onClick={onClose}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
        >
          <X className="h-3.5 w-3.5" />
          Exit compare
        </button>
      </div>

      {/* Body: comments | left video | right video | comments */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0">
        <CommentSidePanel
          side="left"
          versionNumber={leftVersion.version_number}
          comments={leftComments}
          accentColor={LEFT_COLOR}
          focusedCommentId={focusedCommentId}
          onCommentClick={handleCommentClick}
          currentTime={left.currentTime}
        />

        <CompareFrame
          label="Left"
          versions={sorted}
          selected={leftVersion}
          onSelect={setLeftVersion}
          videoRef={left.videoRef}
          streamUrl={leftUrl}
          isLoading={left.isLoading}
          error={left.error}
          accentColor={LEFT_COLOR}
          className="md:border-r border-border"
        />
        <CompareFrame
          label="Right"
          versions={sorted}
          selected={rightVersion}
          onSelect={setRightVersion}
          videoRef={right.videoRef}
          streamUrl={rightUrl}
          isLoading={right.isLoading}
          error={right.error}
          accentColor={RIGHT_COLOR}
          className="md:border-l-0 border-t md:border-t-0 border-border"
        />

        <CommentSidePanel
          side="right"
          versionNumber={rightVersion.version_number}
          comments={rightComments}
          accentColor={RIGHT_COLOR}
          focusedCommentId={focusedCommentId}
          onCommentClick={handleCommentClick}
          currentTime={right.currentTime}
        />
      </div>

      {/* Shared control bar */}
      <div className="shrink-0 border-t border-border bg-bg-secondary px-3 py-2">
        <div className="flex items-center gap-3">
          <button
            onClick={togglePlay}
            disabled={!leftUrl || !rightUrl}
            className="flex items-center justify-center h-9 w-9 rounded-full bg-[var(--accent)] text-white hover:opacity-90 transition-opacity disabled:opacity-40"
            aria-label={left.isPlaying ? 'Pause' : 'Play'}
          >
            {left.isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4 ml-0.5" />}
          </button>

          <CompareTimeline
            currentTime={left.currentTime}
            duration={safeDuration}
            leftComments={leftComments}
            rightComments={rightComments}
            leftColor={LEFT_COLOR}
            rightColor={RIGHT_COLOR}
            focusedCommentId={focusedCommentId}
            onSeek={handleSeek}
            onCommentClick={handleCommentClick}
            className="flex-1"
          />

          <div className="tabular-nums text-xs text-text-secondary shrink-0 min-w-[90px] text-right">
            {formatTime(left.currentTime)} / {formatTime(safeDuration)}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── CompareTimeline: custom scrub bar with per-side comment markers ─────────
// Replaces the plain <input type="range"> so we can render colored dots for
// the left (above) and right (below) version's comments on a single shared
// track. Click on a dot seeks both players and flashes the corresponding row
// in the side panel.
//
// Design: 4px-tall track, 14px row above for left dots, 14px row below for
// right dots, so collisions at the same timecode are visually separable.
// Hover on a dot shows a small floating tooltip with the comment body.

interface CompareTimelineProps {
  currentTime: number
  duration: number
  leftComments: CommentWithReplies[]
  rightComments: CommentWithReplies[]
  leftColor: string
  rightColor: string
  focusedCommentId: string | null
  onSeek: (t: number) => void
  onCommentClick: (c: CommentWithReplies) => void
  className?: string
}

function CompareTimeline({
  currentTime,
  duration,
  leftComments,
  rightComments,
  leftColor,
  rightColor,
  focusedCommentId,
  onSeek,
  onCommentClick,
  className,
}: CompareTimelineProps) {
  const trackRef = React.useRef<HTMLDivElement>(null)
  const [isDragging, setIsDragging] = React.useState(false)
  const [hoveredId, setHoveredId] = React.useState<string | null>(null)

  const timeToPercent = React.useCallback(
    (t: number): number => {
      if (!duration) return 0
      return Math.max(0, Math.min(100, (t / duration) * 100))
    },
    [duration],
  )

  const getTimeFromClientX = React.useCallback(
    (clientX: number): number => {
      const track = trackRef.current
      if (!track || !duration) return 0
      const rect = track.getBoundingClientRect()
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
      return ratio * duration
    },
    [duration],
  )

  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(true)
    onSeek(getTimeFromClientX(e.clientX))
  }

  React.useEffect(() => {
    if (!isDragging) return
    const onMove = (e: MouseEvent) => onSeek(getTimeFromClientX(e.clientX))
    const onUp = (e: MouseEvent) => {
      setIsDragging(false)
      onSeek(getTimeFromClientX(e.clientX))
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [isDragging, getTimeFromClientX, onSeek])

  const playPercent = timeToPercent(currentTime)

  return (
    <div className={cn('relative flex flex-col gap-0 py-1 select-none', className)}>
      {/* Left-side markers (above the track) */}
      <div className="relative h-3.5 w-full">
        {leftComments.map((c) =>
          c.timecode_start === null ? null : (
            <Marker
              key={c.id}
              comment={c}
              leftPercent={timeToPercent(c.timecode_start)}
              color={leftColor}
              side="above"
              isHovered={hoveredId === c.id}
              isFocused={focusedCommentId === c.id}
              onHover={(id) => setHoveredId(id)}
              onClick={() => onCommentClick(c)}
            />
          ),
        )}
      </div>

      {/* Track */}
      <div
        ref={trackRef}
        className="relative h-1.5 w-full rounded-full bg-bg-tertiary cursor-pointer"
        onMouseDown={handleMouseDown}
      >
        {/* Playback progress */}
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${playPercent}%`,
            background: 'linear-gradient(90deg, #6366f1, #818cf8)',
          }}
        />
        {/* Playhead thumb */}
        <div
          className="absolute top-1/2 w-3 h-3 rounded-full bg-white shadow-md pointer-events-none"
          style={{ left: `${playPercent}%`, transform: 'translate(-50%, -50%)' }}
        />
      </div>

      {/* Right-side markers (below the track) */}
      <div className="relative h-3.5 w-full">
        {rightComments.map((c) =>
          c.timecode_start === null ? null : (
            <Marker
              key={c.id}
              comment={c}
              leftPercent={timeToPercent(c.timecode_start)}
              color={rightColor}
              side="below"
              isHovered={hoveredId === c.id}
              isFocused={focusedCommentId === c.id}
              onHover={(id) => setHoveredId(id)}
              onClick={() => onCommentClick(c)}
            />
          ),
        )}
      </div>
    </div>
  )
}

interface MarkerProps {
  comment: CommentWithReplies
  leftPercent: number
  color: string
  side: 'above' | 'below'
  isHovered: boolean
  isFocused: boolean
  onHover: (id: string | null) => void
  onClick: () => void
}

function Marker({
  comment,
  leftPercent,
  color,
  side,
  isHovered,
  isFocused,
  onHover,
  onClick,
}: MarkerProps) {
  const authorName =
    comment.author?.name ?? comment.guest_author?.name ?? 'Unknown'
  return (
    <div
      className="absolute top-0 -translate-x-1/2 cursor-pointer group/marker"
      style={{ left: `${leftPercent}%` }}
      onMouseEnter={() => onHover(comment.id)}
      onMouseLeave={() => onHover(null)}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
    >
      <div
        className={cn(
          'w-2.5 h-2.5 rounded-full ring-2 ring-bg-secondary transition-transform',
          isFocused ? 'scale-150 ring-white' : 'group-hover/marker:scale-125',
        )}
        style={{ backgroundColor: color }}
        title={`${authorName} · ${comment.timecode_start !== null ? formatTimecode(comment.timecode_start) : ''}`}
      />
      {isHovered && comment.timecode_start !== null && (
        <div
          className={cn(
            'absolute left-1/2 -translate-x-1/2 z-50 w-56 pointer-events-none',
            side === 'above' ? 'bottom-full mb-1' : 'top-full mt-1',
          )}
        >
          <div className="rounded-md border border-border bg-bg-elevated shadow-2xl p-2">
            <div className="flex items-center gap-1.5 mb-1">
              <span
                className="inline-block h-2 w-2 rounded-full shrink-0"
                style={{ backgroundColor: color }}
              />
              <span className="text-[11px] text-text-primary font-medium truncate">
                {authorName}
              </span>
              <span className="ml-auto text-[10px] font-mono text-text-tertiary">
                {formatTimecode(comment.timecode_start)}
              </span>
            </div>
            <p className="text-[11px] text-text-secondary line-clamp-2 leading-snug">
              {comment.body}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

// ── CommentSidePanel: per-version comment list ──────────────────────────────

interface CommentSidePanelProps {
  side: 'left' | 'right'
  versionNumber: number
  comments: CommentWithReplies[]
  accentColor: string
  focusedCommentId: string | null
  onCommentClick: (c: CommentWithReplies) => void
  currentTime: number
}

function CommentSidePanel({
  side,
  versionNumber,
  comments,
  accentColor,
  focusedCommentId,
  onCommentClick,
  currentTime,
}: CommentSidePanelProps) {
  return (
    <aside
      className={cn(
        // Hidden on mobile — Compare Mode is desktop-only practically;
        // surfacing four columns on a phone would be unusable.
        'hidden md:flex shrink-0 w-[260px] flex-col min-h-0 bg-bg-secondary/40',
        side === 'left' ? 'border-r border-border' : 'border-l border-border',
      )}
    >
      <div className="shrink-0 flex items-center gap-2 px-3 h-9 border-b border-border">
        <span
          className="inline-block h-2 w-2 rounded-full shrink-0"
          style={{ backgroundColor: accentColor }}
        />
        <MessageSquare className="h-3.5 w-3.5 text-text-tertiary" />
        <span className="text-[11px] uppercase tracking-wider text-text-tertiary">
          v{versionNumber}
        </span>
        <span className="ml-auto text-[11px] text-text-tertiary tabular-nums">
          {comments.length}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {comments.length === 0 ? (
          <div className="flex h-full items-center justify-center px-4">
            <p className="text-[11px] text-text-tertiary text-center leading-relaxed">
              No timecoded comments on this version yet.
            </p>
          </div>
        ) : (
          <ul className="py-1">
            {comments.map((c) => (
              <CommentRow
                key={c.id}
                comment={c}
                accentColor={accentColor}
                isFocused={focusedCommentId === c.id}
                isCurrent={
                  c.timecode_start !== null &&
                  Math.abs(currentTime - c.timecode_start) < 0.5
                }
                onClick={() => onCommentClick(c)}
              />
            ))}
          </ul>
        )}
      </div>
    </aside>
  )
}

interface CommentRowProps {
  comment: CommentWithReplies
  accentColor: string
  isFocused: boolean
  isCurrent: boolean
  onClick: () => void
}

function CommentRow({ comment, accentColor, isFocused, isCurrent, onClick }: CommentRowProps) {
  const authorName =
    comment.author?.name ?? comment.guest_author?.name ?? 'Unknown'
  return (
    <li>
      <button
        onClick={onClick}
        className={cn(
          'w-full text-left flex flex-col gap-1 px-3 py-2 border-l-2 transition-colors',
          isFocused
            ? 'bg-bg-hover'
            : isCurrent
              ? 'bg-bg-hover/40'
              : 'hover:bg-bg-hover/60',
        )}
        style={{
          borderLeftColor: isFocused || isCurrent ? accentColor : 'transparent',
        }}
      >
        <div className="flex items-center gap-1.5 text-[11px]">
          <span
            className="font-mono px-1.5 py-0.5 rounded text-white tabular-nums"
            style={{ backgroundColor: accentColor }}
          >
            {comment.timecode_start !== null
              ? formatTimecode(comment.timecode_start)
              : '--:--'}
          </span>
          <span className="text-text-tertiary truncate">{authorName}</span>
          {comment.resolved && (
            <CheckCircle2 className="h-3 w-3 text-status-success shrink-0" />
          )}
        </div>
        <p className="text-xs text-text-secondary line-clamp-2 leading-snug">
          {comment.body}
        </p>
      </button>
    </li>
  )
}

function filterTimecoded(comments: CommentWithReplies[]): CommentWithReplies[] {
  // Only top-level (no parent_id), timecoded, unresolved comments are
  // surfaced on the compare timeline. Replies stay attached to the parent;
  // resolved notes are noise during a comparison pass.
  return [...comments]
    .filter(
      (c) =>
        c.parent_id === null &&
        c.timecode_start !== null &&
        !c.resolved,
    )
    .sort((a, b) => (a.timecode_start ?? 0) - (b.timecode_start ?? 0))
}

// ── CompareFrame: bare video element + version picker, no controls ──────────

interface FrameProps {
  label: string
  versions: AssetVersion[]
  selected: AssetVersion
  onSelect: (v: AssetVersion) => void
  videoRef: React.RefObject<HTMLVideoElement>
  streamUrl: string | null
  isLoading: boolean
  error: string | null
  accentColor: string
  className?: string
}

function CompareFrame({
  label,
  versions,
  selected,
  onSelect,
  videoRef,
  streamUrl,
  isLoading,
  error,
  accentColor,
  className,
}: FrameProps) {
  return (
    <div className={cn('flex-1 flex flex-col min-h-0 min-w-0', className)}>
      <div className="shrink-0 flex items-center justify-between px-3 h-9 border-b border-border bg-bg-secondary/50">
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full shrink-0"
            style={{ backgroundColor: accentColor }}
          />
          <span className="text-[11px] uppercase tracking-wider text-text-tertiary">{label}</span>
        </div>
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
                const cfg = statusCfg[v.processing_status]
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

      <div className="flex-1 relative bg-black flex items-center justify-center min-h-0">
        <video
          ref={videoRef}
          className="max-h-full max-w-full"
          playsInline
          muted
        />

        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <Loader2 className="h-6 w-6 animate-spin text-zinc-500" />
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/60">
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        )}

        {/* Version badge on the frame, tinted to match the side color. */}
        <div
          className="absolute top-3 left-3 inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-white pointer-events-none"
          style={{ backgroundColor: `${accentColor}cc` }}
        >
          v{selected.version_number}
        </div>
      </div>
    </div>
  )
}

// ── Stream URL pre-fetch hook ───────────────────────────────────────────────

function useStreamUrl(assetId: string, versionId: string): string | null {
  const [url, setUrl] = React.useState<string | null>(null)
  React.useEffect(() => {
    let cancelled = false
    setUrl(null)
    api
      .get<{ url: string }>(`/assets/${assetId}/stream?version_id=${versionId}`)
      .then((data) => {
        if (cancelled) return
        const resolved = data.url.startsWith('/')
          ? `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}${data.url}`
          : data.url
        setUrl(resolved)
      })
      .catch(() => {
        /* error surfaces via useVideoPlayer once it has no src */
      })
    return () => {
      cancelled = true
    }
  }, [assetId, versionId])
  return url
}
