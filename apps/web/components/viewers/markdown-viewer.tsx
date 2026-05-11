'use client'

/**
 * MarkdownViewer
 * --------------
 * Read-only renderer for `document` assets (Markdown in v1).
 *
 * Pipeline:
 *   1. Fetch the raw .md text from a presigned S3/R2 URL.
 *   2. Pass to react-markdown with:
 *        - remark-gfm (GitHub-flavored tables, task-lists, autolinks)
 *        - rehype-sanitize (strips <script>, <iframe>, on*-handlers, etc.)
 *   3. Style via Tailwind utility classes — the FreeFrame UI is dark.
 *
 * Why sanitize:
 *   Markdown lets users embed raw HTML. Without a sanitizer, a hostile script
 *   uploader could inject `<script>` and steal cookies / session tokens. We
 *   use rehype-sanitize with the default schema, which already strips
 *   script/style/iframe/object/embed and `on*` attributes.
 *
 * Why we keep this dumb:
 *   No editing, no comments-on-line, no version-diff. Those land in v2/v4.
 */
import * as React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeSanitize from 'rehype-sanitize'
import { Loader2, AlertTriangle, FileText } from 'lucide-react'
import { cn } from '@/lib/utils'

interface MarkdownViewerProps {
  /** Presigned URL to the raw .md file in S3/R2. */
  url: string | null
  /** Optional class for the outer wrapper. */
  className?: string
  /** Show a loading spinner before the URL resolves. */
  loading?: boolean
}

const MAX_SIZE_BYTES = 5 * 1024 * 1024 // mirrors apps/api/schemas/upload.py:MAX_DOCUMENT_SIZE_BYTES

export function MarkdownViewer({ url, className, loading }: MarkdownViewerProps) {
  const [content, setContent] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [fetching, setFetching] = React.useState(false)

  React.useEffect(() => {
    if (!url) return
    let cancelled = false
    setFetching(true)
    setError(null)
    fetch(url)
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const contentLength = res.headers.get('content-length')
        if (contentLength && Number(contentLength) > MAX_SIZE_BYTES) {
          throw new Error('Document exceeds 5 MB size limit')
        }
        return res.text()
      })
      .then((text) => {
        if (!cancelled) setContent(text)
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message || 'Failed to load document')
      })
      .finally(() => {
        if (!cancelled) setFetching(false)
      })
    return () => {
      cancelled = true
    }
  }, [url])

  if (loading || fetching) {
    return (
      <div className={cn('flex items-center justify-center h-full', className)}>
        <Loader2 className="h-6 w-6 animate-spin text-zinc-500" />
      </div>
    )
  }

  if (error) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 h-full text-zinc-500', className)}>
        <AlertTriangle className="h-8 w-8 text-amber-500/70" />
        <p className="text-sm">Couldn't load this document</p>
        <p className="text-xs text-zinc-600">{error}</p>
      </div>
    )
  }

  if (!url) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 h-full text-zinc-500', className)}>
        <FileText className="h-10 w-10 text-zinc-700" />
        <p className="text-sm">Document unavailable</p>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'h-full overflow-y-auto px-6 md:px-10 py-8 bg-bg-primary',
        className,
      )}
    >
      <article
        className={cn(
          // Width cap + center: long-form text is unreadable across full screen
          'mx-auto max-w-3xl text-[15px] leading-relaxed text-text-primary',
          // Headings
          '[&_h1]:text-3xl [&_h1]:font-semibold [&_h1]:mt-8 [&_h1]:mb-4 [&_h1]:text-text-primary',
          '[&_h2]:text-2xl [&_h2]:font-semibold [&_h2]:mt-6 [&_h2]:mb-3 [&_h2]:text-text-primary',
          '[&_h3]:text-xl  [&_h3]:font-semibold [&_h3]:mt-5 [&_h3]:mb-2 [&_h3]:text-text-primary',
          '[&_h4]:text-lg  [&_h4]:font-semibold [&_h4]:mt-4 [&_h4]:mb-2 [&_h4]:text-text-primary',
          // Paragraphs + inline text
          '[&_p]:my-3 [&_p]:text-text-secondary',
          '[&_strong]:text-text-primary [&_strong]:font-semibold',
          '[&_em]:italic',
          // Links
          '[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:opacity-80',
          // Lists
          '[&_ul]:list-disc [&_ul]:ml-6 [&_ul]:my-3 [&_ul]:text-text-secondary',
          '[&_ol]:list-decimal [&_ol]:ml-6 [&_ol]:my-3 [&_ol]:text-text-secondary',
          '[&_li]:my-1',
          // Task lists (GFM)
          '[&_li>input[type=checkbox]]:mr-2 [&_li>input[type=checkbox]]:translate-y-[1px]',
          // Code
          '[&_code]:bg-bg-tertiary [&_code]:text-text-primary [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-[13px] [&_code]:font-mono',
          '[&_pre]:bg-bg-tertiary [&_pre]:p-4 [&_pre]:rounded-lg [&_pre]:my-4 [&_pre]:overflow-x-auto',
          '[&_pre_code]:bg-transparent [&_pre_code]:p-0',
          // Blockquote
          '[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-4 [&_blockquote]:my-4 [&_blockquote]:text-text-tertiary [&_blockquote]:italic',
          // Tables (GFM)
          '[&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_table]:text-[14px]',
          '[&_th]:border [&_th]:border-border [&_th]:px-3 [&_th]:py-2 [&_th]:bg-bg-tertiary [&_th]:text-left [&_th]:font-medium',
          '[&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-2 [&_td]:text-text-secondary',
          // HR
          '[&_hr]:border-border [&_hr]:my-6',
          // Images (rare in scripts, but support them — sanitizer permits)
          '[&_img]:max-w-full [&_img]:rounded [&_img]:my-4',
        )}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeSanitize]}
        >
          {content ?? ''}
        </ReactMarkdown>
      </article>
    </div>
  )
}
