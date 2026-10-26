/**
 * The keys the main process takes before the window does. On macOS the application menu's Close
 * Window answers ⌘W, and would close the whole window: the window's own tab closes instead, as a
 * browser's does (the UI review, 26/10/2026); the window closes itself when no tab is on screen.
 * Pure, so it is tested without a window.
 */

/** A key as Electron's `before-input-event` gives it. */
export interface KeyInput {
  alt: boolean
  control: boolean
  key: string
  meta: boolean
  shift: boolean
  type: string
}

/** ⌘W on macOS: what closes the tab on screen rather than the window. */
export function closesTab(input: KeyInput, platform: NodeJS.Platform): boolean {
  return (
    platform === 'darwin' &&
    input.type === 'keyDown' &&
    input.meta &&
    !input.control &&
    !input.alt &&
    !input.shift &&
    input.key.toLowerCase() === 'w'
  )
}
