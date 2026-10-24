/**
 * The sidebar on the left shown or hidden (asked for by the user on 16/10/2026):
 * `mod+b` and a button hide it, leaving a narrow strip that brings it back. The side
 * panel on the right has its own switch (`$panelOpen`, store/terminals.ts).
 *
 * **Their widths** (asked for by the user on 18/10/2026): both open at the same width so the two rails
 * match, and a drag on the edge each has toward the conversation changes it, a double click putting
 * it back. A width is kept in the window's own storage, a look of this window and not
 * of a home, as the theme is; the conversation keeps its own minimum whatever the widths
 * (styles.css).
 */
import { atom } from 'nanostores'

export const $sidebarOpen = atom(true)

export function toggleSidebar(open = !$sidebarOpen.get()): void {
  $sidebarOpen.set(open)
}

export type Rail = 'panel' | 'sidebar'

/** Where both open: 18rem, so a terminal still has columns. */
export const RAIL_DEFAULT = 288
export const RAIL_MIN = 224
export const RAIL_MAX = 640
/** The conversation's own minimum (`.main-pane`, styles.css). */
export const MAIN_MIN = 384

const KEYS: Record<Rail, string> = { panel: 'atlas.panelWidth', sidebar: 'atlas.sidebarWidth' }

/** A width within the rails' bounds, whole pixels. */
export function boundWidth(width: number): number {
  return Math.round(Math.min(RAIL_MAX, Math.max(RAIL_MIN, width)))
}

function stored(rail: Rail): number {
  const found = Number(localStorage.getItem(KEYS[rail]))

  return Number.isFinite(found) && found > 0 ? boundWidth(found) : RAIL_DEFAULT
}

export const $sidebarWidth = atom(stored('sidebar'))
export const $panelWidth = atom(stored('panel'))

function widthOf(rail: Rail) {
  return rail === 'sidebar' ? $sidebarWidth : $panelWidth
}

/**
 * The widest a rail `current` wide may be dragged beside a conversation `main` wide: as far as the
 * conversation keeps its minimum.
 */
export function widest(current: number, main: number): number {
  return boundWidth(current + main - MAIN_MIN)
}

/** Set a rail's width while it is dragged; `keep` writes it down, once the drag ends. */
export function setRailWidth(rail: Rail, width: number, keep = false): void {
  const bounded = boundWidth(width)
  widthOf(rail).set(bounded)

  if (keep) {
    localStorage.setItem(KEYS[rail], String(bounded))
  }
}

/** Back to where both open (a double click on the edge). */
export function resetRailWidth(rail: Rail): void {
  widthOf(rail).set(RAIL_DEFAULT)
  localStorage.removeItem(KEYS[rail])
}
