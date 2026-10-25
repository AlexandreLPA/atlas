/**
 * The app browser's rules (docs/GLOSSARY.md, `app browser`), pure: what an address is, which pages
 * a view may load, what a site may ask of the user's machine, and what the window may ask of the
 * browser. `app-browser.ts` applies them to the views; nothing here touches Electron.
 */

/** The partition of every browser tab: its cookies and logins are its own, not the window's. */
export const BROWSER_PARTITION = 'persist:atlas-browser'

/** What a site may ask for and be given. Everything else (camera, microphone, location,
 * notifications, MIDI, USB, full screen…) is refused: the user never asked a site for them here. */
const GRANTED = new Set(['clipboard-sanitized-write'])

/** Whether a site's permission request is granted. */
export function permissionGranted(permission: string): boolean {
  return GRANTED.has(permission)
}

/** Only web pages load in a tab: never `file:`, `javascript:`, `data:`, `devtools:` or one of the
 * app's own schemes (`atlas-preview:`). `about:blank` is how a tab starts. */
export function pageAllowed(url: string): boolean {
  if (url === 'about:blank') {
    return true
  }

  try {
    const { protocol } = new URL(url)

    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

const LOCAL_HOST = /^(localhost|\[::1\]|127(\.\d{1,3}){3})(:\d+)?([/?#].*)?$/i
const HOST_LIKE = /^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(:\d+)?([/?#].*)?$/i
const ADDRESS_LIKE = /^(\d{1,3}(\.\d{1,3}){3})(:\d+)?([/?#].*)?$/
const WEB_ADDRESS = /^https?:\/\/\S+$/i

/**
 * What the user typed in the address bar, as an address to load, or `null` when it is not one. A
 * full `http(s)` address stays; a bare host gets `https://` (`http://` for this machine's own
 * addresses, where a dev server rarely has a certificate). Anything with another scheme, or words
 * that are no host, is no address: nothing is sent to a search engine on the user's behalf.
 */
export function normalizeAddress(typed: string): null | string {
  const text = typed.trim()
  let address: null | string = null

  if (LOCAL_HOST.test(text) || ADDRESS_LIKE.test(text)) {
    address = `http://${text}`
  } else if (HOST_LIKE.test(text)) {
    address = `https://${text}`
  } else if (WEB_ADDRESS.test(text)) {
    address = text
  }

  try {
    return address !== null && pageAllowed(address) ? new URL(address).href : null
  } catch {
    return null
  }
}

/** What the window asks of the browser. */
export type BrowserCommand =
  | { command: 'back' | 'forward' | 'reload' | 'stop'; id: string }
  | { command: 'close' | 'select'; id: string }
  | { command: 'go'; id: null | string; text: string }
  | { command: 'open' }
  | { command: 'show'; rect: null | Rect }

/** Where the browser's view is, in the window's coordinates. */
export interface Rect {
  height: number
  width: number
  x: number
  y: number
}

const COMMANDS_WITH_ID = new Set(['back', 'close', 'forward', 'reload', 'select', 'stop'])
/** An address longer than this is no address a person typed. */
const MAX_TEXT = 4096
/** A window larger than this does not exist; a bigger number is a bug or a lie. */
const MAX_PIXELS = 20_000

function isRect(value: unknown): value is Rect {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const rect = value as Record<string, unknown>

  return ['height', 'width', 'x', 'y'].every(key => {
    const number = rect[key]

    return typeof number === 'number' && Number.isFinite(number) && Math.abs(number) <= MAX_PIXELS
  })
}

/** What came over the bridge, as a command, or `null` when it is not one the browser knows: the
 * window is trusted no further than its arguments' shape. */
export function parseCommand(value: unknown): BrowserCommand | null {
  if (typeof value !== 'object' || value === null) {
    return null
  }

  const given = value as Record<string, unknown>
  const command = given.command

  if (typeof command !== 'string') {
    return null
  }

  if (COMMANDS_WITH_ID.has(command)) {
    return typeof given.id === 'string' && given.id ? ({ command, id: given.id } as BrowserCommand) : null
  }

  if (command === 'open') {
    return { command }
  }

  if (command === 'go') {
    const id = given.id === null || given.id === undefined ? null : given.id

    if (typeof given.text !== 'string' || given.text.length > MAX_TEXT || (id !== null && typeof id !== 'string')) {
      return null
    }

    return { command, id, text: given.text }
  }

  if (command === 'show') {
    if (given.rect === null) {
      return { command, rect: null }
    }

    return isRect(given.rect) ? { command, rect: given.rect } : null
  }

  return null
}
