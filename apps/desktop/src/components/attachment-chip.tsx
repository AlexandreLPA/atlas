import { useEffect, useState } from 'react'

import { t } from '@/i18n'
import { readPreview } from '@/store/app'
import { type AttachmentKind, formatOf, type Tone } from '@/store/attachments'

/** Each family's badge: a document never looks like a spreadsheet, nor a video like audio. */
const TONE: Record<Tone, string> = {
  archive: 'bg-amber-600 text-white',
  audio: 'bg-pink-500 text-white',
  code: 'bg-violet-600 text-white',
  data: 'bg-emerald-600 text-white',
  ebook: 'bg-teal-600 text-white',
  image: 'bg-sky-500 text-white',
  pdf: 'bg-red-600 text-white',
  slides: 'bg-orange-500 text-white',
  text: 'bg-neutral-500 text-white',
  video: 'bg-fuchsia-600 text-white',
  word: 'bg-blue-600 text-white'
}

export interface ChipProps {
  /** What goes under the name: its size, how far it got, or why it failed. */
  detail: string
  failed?: boolean
  kind: AttachmentKind
  name: string
  onOpen?: () => void
  onRemove?: () => void
  /** 0 to 1 while it sends; `null` once it is there. */
  progress?: null | number
  /** An image's thumbnail. */
  thumbnail?: string
}

/**
 * A file as a chip: a tile with its thumbnail or its format's badge,
 * its name and a line under it, and an × that shows on hover. The badge's color is the format's
 * family, so a PDF, a spreadsheet and a slide deck read apart at a glance.
 */
export function AttachmentChip({ detail, failed, kind, name, onOpen, onRemove, progress, thumbnail }: ChipProps) {
  const format = formatOf(name, kind)
  const sending = progress !== undefined && progress !== null

  return (
    <div
      className="group/chip relative min-w-0 shrink-0"
      data-format={format.label}
      data-kind={kind}
      data-testid="attachment-chip"
      data-tone={format.tone}
      title={failed ? detail : name}
    >
      <button
        aria-label={onOpen ? t.attachments.open(name) : name}
        className={`flex max-w-56 items-center gap-2 rounded-xl border bg-white px-2 py-1.5 text-left transition-colors disabled:cursor-default dark:bg-neutral-900 ${
          failed
            ? 'border-red-400 dark:border-red-700'
            : 'border-neutral-300 hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500'
        }`}
        disabled={!onOpen}
        onClick={onOpen}
        type="button"
      >
        <span
          className={`relative grid size-9 shrink-0 place-items-center overflow-hidden rounded-lg text-[0.6rem] font-bold tracking-wide ${
            thumbnail ? 'bg-neutral-100 dark:bg-neutral-800' : TONE[format.tone]
          }`}
          data-testid="attachment-badge"
        >
          {thumbnail ? (
            <img alt="" className="size-full object-cover" draggable={false} src={thumbnail} />
          ) : (
            format.label
          )}
          {thumbnail && (
            <span
              className={`absolute right-0 bottom-0 rounded-tl px-0.5 text-[0.5rem] leading-3 font-bold ${TONE[format.tone]}`}
            >
              {format.label}
            </span>
          )}
          {sending && (
            <span className="absolute inset-x-0 bottom-0 h-1 bg-black/20">
              <span className="block h-full bg-white" style={{ width: `${String(Math.round(progress * 100))}%` }} />
            </span>
          )}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-xs font-medium">{name}</span>
          <span
            className={`block truncate text-[0.65rem] ${failed ? 'text-red-600 dark:text-red-400' : 'text-neutral-500'}`}
          >
            {detail}
          </span>
        </span>
      </button>
      {onRemove && (
        <button
          aria-label={t.attachments.remove(name)}
          className="absolute -top-1.5 -right-1.5 grid size-4 place-items-center rounded-full border border-neutral-300 bg-white text-[0.6rem] text-neutral-500 opacity-0 shadow-sm transition group-hover/chip:opacity-100 hover:text-neutral-900 focus-visible:opacity-100 dark:border-neutral-700 dark:bg-neutral-900"
          onClick={onRemove}
          type="button"
        >
          ×
        </button>
      )}
    </div>
  )
}

/** An attached image's thumbnail, read where the server is (the side panel's reader, step 15). */
export function useThumbnail(path: string, kind: AttachmentKind): string {
  const [source, setSource] = useState('')
  useEffect(() => {
    if (kind !== 'image' || !path) {
      return
    }

    let live = true
    void readPreview(path).then(read => {
      if (live && read.kind === 'image') {
        setSource(read.dataUrl)
      }
    })

    return () => {
      live = false
    }
  }, [kind, path])

  return source
}
