/**
 * The tabs of the central area (step 9d, decided with the user on 25/10/2026): the conversations,
 * Atlas's and the bots', and the executors' sessions open in one home, in the user's order. A
 * click on a row opens it in the passing tab, in italic, which the next click reuses, as code
 * editors do; the tab stays once it is used (a message sent there) or double-clicked. What a first
 * message makes, a conversation or a session, opens in a tab of its own.
 *
 * The tab on screen is never kept apart: it is the one holding what the screen shows
 * (`screenPlace`), so whatever opens a conversation or a session, a notification's click
 * included, shows its tab. The window keeps each home's tabs, so the app reopens on them and on the
 * one it showed; a closed tab never comes back.
 */
import { atom } from 'nanostores'

import type { Card, OpenSession } from './app'

/** What a tab holds: a conversation by its id, or a session as the screen knows it. */
export type TabPlace = { conversationId: string; kind: 'conversation' } | { kind: 'session'; session: OpenSession }

export interface Tab {
  /** Its own, kept while what it holds changes (a session's ids come as it starts). */
  id: string
  place: TabPlace
  /** The passing tab: the next row clicked takes its place. */
  preview: boolean
  /** What it was last called, for a conversation the list no longer has. */
  title: string
}

/** What the window keeps of a home's tabs. */
export interface SavedTabs {
  /** The tab on screen, by its place in `tabs`; `null`, none was. */
  active: null | number
  tabs: Omit<Tab, 'id'>[]
}

const STORAGE = 'atlas.tabs'

/** A session the screen can name again: its harness's id, or an instance here. A new one has
 * neither until its first message starts it, and no tab until then. */
export function named(session: OpenSession): boolean {
  return session.sessionId !== '' || session.instanceId !== ''
}

/** The same conversation, or the same session: by its id in its harness, else its instance here. */
export function samePlace(a: TabPlace, b: TabPlace): boolean {
  if (a.kind === 'conversation' || b.kind === 'conversation') {
    return a.kind === 'conversation' && b.kind === 'conversation' && a.conversationId === b.conversationId
  }

  const [x, y] = [a.session, b.session]

  return (
    x.executor === y.executor &&
    ((x.sessionId !== '' && x.sessionId === y.sessionId) || (x.instanceId !== '' && x.instanceId === y.instanceId))
  )
}

/** What the screen shows that a tab holds: the conversation open, or the session open once it is
 * named; `null` for a new conversation, a new session or a page. */
export function screenPlace(view: string, selected: null | string, open: null | OpenSession): null | TabPlace {
  if (view === 'conversation') {
    return selected === null ? null : { conversationId: selected, kind: 'conversation' }
  }

  return view === 'session' && open !== null && named(open) ? { kind: 'session', session: open } : null
}

/** The cards waiting for the user away from the screen: in another conversation, or in another
 * session (an executor's are in its session's). */
export function away(cards: Card[], view: string, selected: null | string, open: null | OpenSession): Card[] {
  return cards.filter(card => {
    if (card.answered) {
      return false
    }

    if (card.kind === 'executor-approval' || card.kind === 'executor-question') {
      return !(view === 'session' && open?.instanceId && card.params.instance_id === open.instanceId)
    }

    return !(view === 'conversation' && card.conversationId === selected)
  })
}

/** One home's tabs. */
export class Tabs {
  readonly $tabs = atom<Tab[]>([])
  private seq = 0

  /** The tab holding `place`, the one on screen when `place` is what the screen shows. */
  find(place: null | TabPlace): null | Tab {
    return place === null ? null : (this.$tabs.get().find(tab => samePlace(tab.place, place)) ?? null)
  }

  /** `place` opens: its own tab, or the passing tab, which now holds it. */
  show(place: TabPlace, title: string): void {
    const tabs = this.$tabs.get()
    const found = tabs.findIndex(tab => samePlace(tab.place, place))

    if (found >= 0) {
      this.$tabs.set(tabs.map((tab, index) => (index === found ? { ...tab, place, title: title || tab.title } : tab)))

      return
    }

    const tab: Tab = { id: this.next(), place, preview: true, title }
    const passing = tabs.findIndex(open => open.preview)
    this.$tabs.set(passing >= 0 ? tabs.map((open, index) => (index === passing ? tab : open)) : [...tabs, tab])
  }

  /** What a first message made from the screen's draft: a tab of its own, at the end. */
  made(place: TabPlace, title: string): void {
    if (this.find(place)) {
      this.keep(place)
    } else {
      this.$tabs.set([...this.$tabs.get(), { id: this.next(), place, preview: false, title }])
    }
  }

  /** The tab of `place` stays: it was used. */
  keep(place: TabPlace): void {
    const tab = this.find(place)

    if (tab?.preview) {
      this.$tabs.set(this.$tabs.get().map(open => (open === tab ? { ...open, preview: false } : open)))
    }
  }

  /** The session on screen learned more of itself (its ids as they come, its title, another
   * account): its tab follows it; a new session that started gets a tab of its own. */
  follow(before: OpenSession, next: OpenSession): void {
    const tab = this.find({ kind: 'session', session: before })

    if (tab) {
      const place: TabPlace = { kind: 'session', session: next }
      this.$tabs.set(
        this.$tabs.get().map(open => (open === tab ? { ...open, place, title: next.title || open.title } : open))
      )
    } else if (!named(before) && named(next)) {
      this.made({ kind: 'session', session: next }, next.title)
    }
  }

  /** Close a tab. For the one on screen, what shows instead: the next tab, else the one before,
   * else `null`, a new conversation. */
  close(id: string): null | Tab {
    const tabs = this.$tabs.get()
    const index = tabs.findIndex(tab => tab.id === id)

    if (index < 0) {
      return null
    }

    const rest = tabs.filter(tab => tab.id !== id)
    this.$tabs.set(rest)

    return rest[index] ?? rest[index - 1] ?? null
  }

  /** Close the tabs of what no longer exists (a bot or a session deleted). */
  forget(gone: (place: TabPlace) => boolean): void {
    const tabs = this.$tabs.get()

    if (tabs.some(tab => gone(tab.place))) {
      this.$tabs.set(tabs.filter(tab => !gone(tab.place)))
    }
  }

  /** Put a dragged tab at `to`, among the others. */
  move(id: string, to: number): void {
    const tabs = this.$tabs.get()
    const tab = tabs.find(open => open.id === id)

    if (tab) {
      const rest = tabs.filter(open => open !== tab)
      this.$tabs.set([...rest.slice(0, to), tab, ...rest.slice(to)])
    }
  }

  /** The titles as the lists say them now; a tab the lists do not name keeps its own. */
  retitle(title: (place: TabPlace) => string | undefined): void {
    const tabs = this.$tabs.get()
    const titled = tabs.map(tab => {
      const now = title(tab.place)

      return now && now !== tab.title ? { ...tab, title: now } : tab
    })

    if (titled.some((tab, index) => tab !== tabs[index])) {
      this.$tabs.set(titled)
    }
  }

  /** What the window keeps, with the tab holding `shown`, what the screen shows. */
  saved(shown: null | TabPlace): SavedTabs {
    const tabs = this.$tabs.get()
    const active = this.find(shown)

    return {
      active: active ? tabs.indexOf(active) : null,
      tabs: tabs.map(({ place, preview, title }) => ({ place, preview, title }))
    }
  }

  /** The tabs kept last time, before any opened since; the one that was on screen, if any. */
  restore(saved: null | SavedTabs): null | Tab {
    if (!saved) {
      return null
    }

    const kept = saved.tabs.map(tab => ({ ...tab, id: this.next() }))
    const since = this.$tabs.get().filter(tab => !kept.some(old => samePlace(old.place, tab.place)))
    this.$tabs.set([...kept, ...since])

    return saved.active === null ? null : (kept[saved.active] ?? null)
  }

  private next(): string {
    this.seq += 1

    return `tab-${String(this.seq)}`
  }
}

function readAll(): Record<string, unknown> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE) ?? '{}')

    return stored !== null && typeof stored === 'object' ? (stored as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

/** The tabs the window kept for a home (its machine and name), read leniently: an entry this
 * version cannot read is left out. */
export function readTabs(home: string): null | SavedTabs {
  const saved = readAll()[home]

  if (!saved || typeof saved !== 'object') {
    return null
  }

  const { active, tabs } = saved as { active?: unknown; tabs?: unknown }
  const read: Omit<Tab, 'id'>[] = []
  let shown: null | number = null

  for (const [index, entry] of (Array.isArray(tabs) ? (tabs as unknown[]) : []).entries()) {
    const tab = savedTab(entry)

    if (tab) {
      shown = index === active ? read.length : shown
      read.push(tab)
    }
  }

  return { active: shown, tabs: read }
}

/** Keep a home's tabs, the other homes' as they are. */
export function writeTabs(home: string, saved: SavedTabs): void {
  localStorage.setItem(STORAGE, JSON.stringify({ ...readAll(), [home]: saved }))
}

/** One kept tab as this version reads it, or `null`. */
function savedTab(entry: unknown): null | Omit<Tab, 'id'> {
  const { place, preview, title } = (entry ?? {}) as { place?: unknown; preview?: unknown; title?: unknown }

  if (typeof preview !== 'boolean' || typeof title !== 'string' || !place || typeof place !== 'object') {
    return null
  }

  const { conversationId, kind, session } = place as { conversationId?: unknown; kind?: unknown; session?: unknown }

  if (kind === 'conversation') {
    return typeof conversationId === 'string' && conversationId
      ? { place: { conversationId, kind }, preview, title }
      : null
  }

  const fields = (kind === 'session' && session && typeof session === 'object' ? session : {}) as Record<
    string,
    unknown
  >
  const [elsewhere, executor, folder, instanceId, sessionId, sessionTitle] = [
    'elsewhere',
    'executor',
    'folder',
    'instanceId',
    'sessionId',
    'title'
  ].map(key => fields[key])

  if (
    typeof elsewhere !== 'string' ||
    typeof executor !== 'string' ||
    typeof folder !== 'string' ||
    typeof instanceId !== 'string' ||
    typeof sessionId !== 'string' ||
    typeof sessionTitle !== 'string'
  ) {
    return null
  }

  const open: OpenSession = {
    choice: null,
    elsewhere,
    executor,
    expired: false,
    folder,
    instanceId,
    sessionId,
    title: sessionTitle
  }

  return named(open) ? { place: { kind: 'session', session: open }, preview, title } : null
}
