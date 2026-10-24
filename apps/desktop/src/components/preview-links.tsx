import { useState } from 'react'

import { t } from '@/i18n'
import { current, currentOwner } from '@/store/app'
import { openPreview, previewables, type PreviewTarget } from '@/store/previews'
import type { Thread } from '@/store/thread'

/** The links each conversation dismissed, kept while the app runs. */
const dismissed = new Map<string, Set<string>>()

/**
 * The files the agent wrote and the sites it started, as small links above the box (step 16f:
 * at most 4 a conversation, never opened by themselves). A click opens
 * the preview beside; × dismisses the link.
 */
export function PreviewLinks({ thread }: { thread: Thread }) {
  const owner = currentOwner()
  const [, redraw] = useState(0)
  const gone = dismissed.get(owner) ?? new Set<string>()
  const links = previewables(thread.items).filter(target => !gone.has(key(target)))

  if (links.length === 0) {
    return null
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-wrap gap-2 px-6 pb-2 text-xs" data-testid="preview-links">
      {links.map(target => (
        <span
          className="flex items-center gap-1 rounded-full border border-neutral-300 px-2 py-0.5 dark:border-neutral-700"
          data-testid="preview-link"
          key={key(target)}
        >
          <button
            onClick={() => {
              openPreview(owner, absolute(target), true)
            }}
            title={target.path ?? target.url}
            type="button"
          >
            ◳ {target.label}
          </button>
          <button
            aria-label={t.preview.dismiss}
            className="text-neutral-400 hover:text-neutral-700"
            onClick={() => {
              dismissed.set(owner, new Set([...gone, key(target)]))
              redraw(count => count + 1)
            }}
            type="button"
          >
            ×
          </button>
        </span>
      ))}
    </div>
  )
}

function key(target: PreviewTarget): string {
  return target.path ?? target.url ?? target.label
}

/** A file the agent named from its folder, read where it is. */
function absolute(target: PreviewTarget): PreviewTarget {
  const path = target.path

  if (!path || /^([A-Za-z]:[\\/]|\/|\\\\)/.test(path)) {
    return target
  }

  const folder = current().workspace
  const separator = folder.includes('\\') ? '\\' : '/'

  return { ...target, path: `${folder.replace(/[\\/]+$/, '')}${separator}${path}` }
}
