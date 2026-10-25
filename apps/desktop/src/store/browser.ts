/**
 * The side panel's browser, as the window sees it (docs/GLOSSARY.md, `app browser`): the browser
 * tabs the main process keeps (electron/app-browser.ts), told as they change. The pages live in
 * native views the main process lays over the panel; this holds only what the toolbar and the tabs
 * show.
 *
 * Today the tabs are one list for the whole app. A later step ties them to conversations, as the
 * previews' are (store/previews.ts), by filtering this list by owner.
 */
import { atom } from 'nanostores'

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
