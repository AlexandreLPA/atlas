/**
 * The side panel's previews (step 16f, decided with the user on 15/10/2026): a file, a local site
 * or an artifact (a block of HTML, mermaid or Markdown of an answer), a tab each.
 *
 * - **Each tab is the conversation's that opened it**: the panel shows the tabs of
 *   what is on screen, and another conversation's stay until it comes back.
 * - **It never opens by itself**: "Open beside" on a block, a link above
 *   the box for a file written or a site started, or the agent's `desktop_preview`, which never
 *   takes the focus from a conversation in the background.
 */
import { atom } from 'nanostores'

import { $panelOpen, $panelTab } from './terminals'

export { $panelTab }

export interface PreviewTarget {
  /** An artifact's text: it has nothing behind it on disk or on the network. */
  content?: string
  kind: 'artifact' | 'file' | 'url'
  label: string
  /** An artifact's language: `html`, `mermaid` or `markdown`. */
  language?: string
  /** A file's absolute path, on this machine. */
  path?: string
  /** A web address (a local site). */
  url?: string
}

export interface PreviewTab {
  id: string
  /** Whose tab it is: a conversation's id, or `session:<id>` for an executor's session. */
  owner: string
  target: PreviewTarget
}

export const $previews = atom<PreviewTab[]>([])

let counter = 0

/** What the same preview is opened from twice: its source. */
export function sourceOf(target: PreviewTarget): string {
  return target.kind === 'artifact'
    ? `artifact:${target.language ?? ''}:${target.content ?? ''}`
    : `${target.kind}:${target.path ?? target.url ?? ''}`
}

/** Open a preview for `owner`; `focus` shows it now (a click, or the agent on the conversation on
 * screen), else it waits among its conversation's tabs. The same source opens once. */
export function openPreview(owner: string, target: PreviewTarget, focus: boolean): PreviewTab {
  const source = sourceOf(target)
  const found = $previews.get().find(tab => tab.owner === owner && sourceOf(tab.target) === source)
  counter += 1
  const tab = found ?? { id: `preview-${String(counter)}`, owner, target }

  if (!found) {
    $previews.set([...$previews.get(), tab])
  }

  if (focus) {
    $panelTab.set(tab.id)
    $panelOpen.set(true)
  }

  return tab
}

/** Close one of `owner`'s previews, by id or by what it shows; every one when neither is given. */
export function closePreview(owner: string, which?: { id?: string; target?: string }): void {
  const kept = $previews.get().filter(tab => {
    if (tab.owner !== owner) {
      return true
    }

    if (which?.id) {
      return tab.id !== which.id
    }

    if (which?.target) {
      return tab.target.path !== which.target && tab.target.url !== which.target
    }

    return false
  })
  $previews.set(kept)
}

/** A target the agent named (`desktop_preview`): a web address, or a file's path. */
export function targetOf(raw: string, label = ''): PreviewTarget {
  const named = label || raw.split(/[\\/]/).filter(Boolean).pop() || raw

  return /^https?:\/\//i.test(raw) ? { kind: 'url', label: named, url: raw } : { kind: 'file', label: named, path: raw }
}

/**
 * The files written and the sites started in a conversation, the latest 4, for the links
 * above the box: a write of an HTML, Markdown or SVG file, and a local address in a command's output.
 */
export function previewables(
  items: { args?: string; kind: string; name?: string; result?: string }[]
): PreviewTarget[] {
  const found = new Map<string, PreviewTarget>()

  for (const item of items) {
    if (item.kind !== 'tool') {
      continue
    }

    if (item.name === 'write_file' || item.name === 'edit_file') {
      const path = pathOf(item.args ?? '')

      if (path && /\.(html?|md|markdown|svg)$/i.test(path)) {
        found.delete(path)
        found.set(path, targetOf(path))
      }
    }

    for (const url of (item.result ?? '').match(
      /https?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?[^\s"'<>)]*/gi
    ) ?? []) {
      const clean = url.replace(/[.,;]+$/, '').replace('0.0.0.0', 'localhost')
      found.delete(clean)
      found.set(clean, targetOf(clean))
    }
  }

  return [...found.values()].slice(-4)
}

function pathOf(args: string): string {
  try {
    const parsed: unknown = JSON.parse(args)

    return parsed && typeof parsed === 'object' && 'path' in parsed && typeof parsed.path === 'string'
      ? parsed.path
      : ''
  } catch {
    return ''
  }
}
