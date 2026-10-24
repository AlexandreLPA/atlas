/**
 * The executors' folders the user archived, out of the sidebar's list so it stays short (asked for
 * by the user on 18/10/2026), never touching the folder, kept in the window's storage and apart per
 * home and executor so one home's hiding does not bleed into another's. An archived folder stays
 * listed under the executor's "Archived", where it comes back. A session is archived the same way
 * (asked for by the user on 19/10/2026), hidden and kept whole, under its folder's "Archived".
 */
import { atom } from 'nanostores'

const KEY = 'atlas.archivedFolders'
const SESSIONS_KEY = 'atlas.archivedSessions'

function read<T = string[]>(key: string = KEY): Record<string, T> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(key) ?? '{}')

    return stored !== null && typeof stored === 'object' ? (stored as Record<string, T>) : {}
  } catch {
    return {}
  }
}

/** By `home\u0000executor`, the folders archived. */
export const $archived = atom<Record<string, string[]>>(read())

export function archiveKey(home: null | string, executor: string): string {
  return `${home ?? ''}\u0000${executor}`
}

export function setArchived(home: null | string, executor: string, folder: string, archived: boolean): void {
  const key = archiveKey(home, executor)
  const all = $archived.get()
  const kept = (all[key] ?? []).filter(found => found !== folder)
  const next = { ...all, [key]: archived ? [...kept, folder] : kept }
  $archived.set(next)
  localStorage.setItem(KEY, JSON.stringify(next))
}

/** By `home\u0000executor`, the sessions archived, by id. */
export const $archivedSessions = atom<Record<string, string[]>>(read(SESSIONS_KEY))

export function setSessionArchived(home: null | string, executor: string, session: string, archived: boolean): void {
  const key = archiveKey(home, executor)
  const all = $archivedSessions.get()
  const kept = (all[key] ?? []).filter(found => found !== session)
  const next = { ...all, [key]: archived ? [...kept, session] : kept }
  $archivedSessions.set(next)
  localStorage.setItem(SESSIONS_KEY, JSON.stringify(next))
}

const SHOWN_KEY = 'atlas.showArchived'

/** By `home\u0000executor`, whether its archived folders and sessions are listed at all (asked for by
 * the user on 21/10/2026, to keep the sidebar clean): off until turned on in its row's menu. */
export const $showArchived = atom<Record<string, boolean>>(read<boolean>(SHOWN_KEY))

export function setShowArchived(home: null | string, executor: string, shown: boolean): void {
  const next = { ...$showArchived.get(), [archiveKey(home, executor)]: shown }
  $showArchived.set(next)
  localStorage.setItem(SHOWN_KEY, JSON.stringify(next))
}
