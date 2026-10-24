/**
 * The order the user dragged the executors and their accounts into, in the sidebar (asked for by
 * the user on 22/10/2026, where they kept the order they were added in). Kept in the window's
 * storage and apart per home, as the archived folders are (store/archive.ts); an executor not in
 * it yet, just added or found, comes after those that are, in the order it was added in.
 */
import { atom } from 'nanostores'

const KEY = 'atlas.executorOrder'

function read(): Record<string, string[]> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}')

    return stored !== null && typeof stored === 'object' ? (stored as Record<string, string[]>) : {}
  } catch {
    return {}
  }
}

/** By home, the executors' names in the order the user left them. */
export const $executorOrder = atom<Record<string, string[]>>(read())

/** The rows in the order kept, those it does not name after, as they came. */
export function inOrder<T>(rows: readonly T[], name: (row: T) => string, order: readonly string[]): T[] {
  const at = (row: T) => {
    const found = order.indexOf(name(row))

    return found < 0 ? order.length : found
  }

  return rows
    .map((row, index) => ({ at: at(row), index, row }))
    .sort((a, b) => a.at - b.at || a.index - b.index)
    .map(({ row }) => row)
}

/** The list with `block` moved, whole, just before `anchor` or just after it. */
export function moveBlock(list: readonly string[], block: readonly string[], anchor: string, after: boolean): string[] {
  if (block.includes(anchor) || !list.includes(anchor)) {
    return [...list]
  }

  const rest = list.filter(name => !block.includes(name))
  const at = rest.indexOf(anchor) + (after ? 1 : 0)

  return [...rest.slice(0, at), ...block, ...rest.slice(at)]
}

/** Keep the order shown, those it leaves out (turned off, not found now) after it as they were. */
export function setExecutorOrder(home: null | string, shown: readonly string[]): void {
  const key = home ?? ''
  const all = $executorOrder.get()
  const next = { ...all, [key]: [...shown, ...(all[key] ?? []).filter(name => !shown.includes(name))] }
  $executorOrder.set(next)
  localStorage.setItem(KEY, JSON.stringify(next))
}
