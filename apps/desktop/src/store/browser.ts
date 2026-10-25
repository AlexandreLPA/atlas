/**
 * The side panel's browser, as the window sees it (docs/GLOSSARY.md, `app browser`): the browser
 * tabs the main process keeps (electron/app-browser.ts), told as they change. The pages live in
 * native views the main process lays over the panel; this holds only what the toolbar and the tabs
 * show.
 *
 * Today the tabs are one list for the whole app. A later step ties them to conversations, as the
 * previews' are (store/previews.ts), by filtering this list by owner.
 */
import { atom, computed } from 'nanostores'

import { $panelOpen, $panelTab } from './terminals'

/** The panel's tab with the browser, in `$panelTab` (store/terminals.ts). */
export const BROWSER_TAB = 'browser'

export const $browser = atom<BrowserState>({ selected: null, tabs: [] })

/** The browser the app brings: none in the tests' window, nor outside the app. */
export function browserHost(): BrowserBridge | undefined {
  return (window as Partial<Window>).atlas?.browser
}

/** Follow the browser's state until the returned function is called. */
export function followBrowser(): () => void {
  const host = browserHost()

  if (!host) {
    return () => undefined
  }

  let live = true
  void host.state().then(state => {
    if (live) {
      $browser.set(state)
    }
  })
  const stop = host.onChange(state => {
    $browser.set(state)
  })

  return () => {
    live = false
    stop()
  }
}

/** Ask the browser for something; `null` where there is none. */
export async function browserCommand(command: BrowserCommand): Promise<BrowserAnswer | null> {
  return (await browserHost()?.command(command)) ?? null
}

/** The browser asked to take the conversation's place (its header's expand button). */
export const $browserWanted = atom(false)

/** Whether it does: asked for, the panel open and on the browser. The panel keeps its place beside
 * the conversation on any other tab, and goes back to it when the browser is left. */
export const $browserExpanded = computed(
  [$browserWanted, $panelOpen, $panelTab],
  (wanted, open, tab) => wanted && open && tab === BROWSER_TAB
)

export function toggleBrowserExpanded(): void {
  $browserWanted.set(!$browserWanted.get())
}

/** Show the panel on the browser, from the globe or its shortcut. */
export function openBrowser(): void {
  $panelOpen.set(true)
  $panelTab.set(BROWSER_TAB)
}

/** The globe's switch: the browser when it is not on screen, else the panel hides. */
export function toggleBrowser(): void {
  if ($panelOpen.get() && $panelTab.get() === BROWSER_TAB) {
    $panelOpen.set(false)
  } else {
    openBrowser()
  }
}

/** The ports where a web server answers on this machine, the common ones and `extra`. */
export async function lookForServers(extra: number[]): Promise<number[]> {
  return (await browserHost()?.servers(extra)) ?? []
}
