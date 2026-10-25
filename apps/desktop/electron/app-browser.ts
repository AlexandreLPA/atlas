/**
 * The app browser (docs/GLOSSARY.md, `app browser`): the side panel's Browser tab, a page of the
 * web in a native view laid over the panel, one `WebContentsView` per browser tab.
 *
 * - **A site reaches nothing of Atlas.** Every view is in the `persist:atlas-browser` partition,
 *   with no preload, the sandbox, context isolation and no Node. Camera, microphone, location,
 *   notifications and the rest are refused (`app-browser-rules.ts`); only `http(s)` pages load, a
 *   redirect or a link to any other scheme is stopped; a download is refused (the browser has no
 *   place to put one yet).
 * - **The window's own commands are the only way in.** `registerAppBrowser` answers the two
 *   channels of the preload, from the window alone, and each command is checked for its shape
 *   before it is run.
 * - **The agent will drive the same views** (the bridge, a later step): every view keeps a
 *   `webContents.debugger` attached, which never reaches the window's own page. Found by the
 *   feasibility test of 24/10/2026, a view that is hidden, laid off the window or in a minimized
 *   window ignores `Input.*` and never answers `Page.captureScreenshot`, unless it is told it has
 *   the focus (`Emulation.setFocusEmulationEnabled`), is active (`Page.setWebLifecycleState`) and
 *   is not throttled; so each view gets the three as it is made and again after each navigation.
 * - **A pop-up is a tab**, made from the `webContents` Electron hands over, so `window.opener`
 *   stays (a sign-in through a pop-up needs it).
 */
import { type BrowserWindow, type IpcMain, session, type WebContents, WebContentsView } from 'electron'

import {
  BROWSER_PARTITION,
  type BrowserCommand,
  normalizeAddress,
  pageAllowed,
  parseCommand,
  permissionGranted,
  type Rect
} from './app-browser-rules'
import { lookForServers, parsePorts, portsToLook } from './local-servers'

/** A browser tab as the window shows it. */
export interface BrowserTabState {
  canGoBack: boolean
  canGoForward: boolean
  /** Why the page did not load, or `''`. */
  error: string
  id: string
  loading: boolean
  title: string
  url: string
}

/** The browser as the window sees it. */
export interface BrowserState {
  selected: null | string
  tabs: BrowserTabState[]
}

/** What a command came to. */
export type BrowserAnswer =
  { id?: string; ok: true } | { ok: false; reason: 'address' | 'closed' | 'invalid' | 'unknown' }

interface Tab {
  error: string
  id: string
  view: WebContentsView
}

/** The channel the browser tells the window its state on. */
export const BROWSER_CHANGED = 'atlas:browser:changed'

/** What Chromium reports for a load a newer one replaced: not an error. */
const ABORTED = -3

/** Refuse what a site may ask the machine for, and any download; once, for the partition. */
export function configureBrowserSession(): void {
  const browsing = session.fromPartition(BROWSER_PARTITION)
  browsing.setPermissionRequestHandler((_contents, permission, callback) => {
    callback(permissionGranted(permission))
  })
  browsing.setPermissionCheckHandler((_contents, permission) => permissionGranted(permission))
  browsing.setDevicePermissionHandler(() => false)
  browsing.on('will-download', event => {
    event.preventDefault()
  })
}

export class AppBrowser {
  private counter = 0
  private rect: null | Rect = null
  private selected: null | string = null
  private readonly tabs = new Map<string, Tab>()
  private tell: null | ReturnType<typeof setImmediate> = null

  constructor(
    private readonly window: BrowserWindow,
    private readonly log: (message: string) => void = () => undefined
  ) {
    window.on('closed', () => {
      this.dispose()
    })
  }

  /** The window's page: the only one the browser answers. */
  get owner(): WebContents {
    return this.window.webContents
  }

  snapshot(): BrowserState {
    return {
      selected: this.selected,
      tabs: [...this.tabs.values()].map(tab => this.stateOf(tab))
    }
  }

  /** Run a command from the window. */
  run(command: BrowserCommand): BrowserAnswer {
    switch (command.command) {
      case 'open': {
        return { id: this.open().id, ok: true }
      }

      case 'go': {
        return this.go(command.id, command.text)
      }

      case 'show': {
        this.rect = command.rect
          ? {
              height: Math.max(0, Math.round(command.rect.height)),
              width: Math.max(0, Math.round(command.rect.width)),
              x: Math.round(command.rect.x),
              y: Math.round(command.rect.y)
            }
          : null
        this.layout()

        return { ok: true }
      }

      case 'select': {
        if (!this.tabs.has(command.id)) {
          return { ok: false, reason: 'unknown' }
        }

        this.selected = command.id
        this.layout()
        this.changed()

        return { ok: true }
      }

      case 'close': {
        const tab = this.tabs.get(command.id)

        if (!tab) {
          return { ok: false, reason: 'unknown' }
        }

        this.remove(tab, true)

        return { ok: true }
      }

      default: {
        return this.navigate(command.command, command.id)
      }
    }
  }

  /** Every view gone, with the window. */
  dispose(): void {
    if (this.tell !== null) {
      clearImmediate(this.tell)
      this.tell = null
    }

    for (const tab of [...this.tabs.values()]) {
      this.remove(tab, true, false)
    }
  }

  private go(id: null | string, text: string): BrowserAnswer {
    const address = normalizeAddress(text)

    if (address === null) {
      return { ok: false, reason: 'address' }
    }

    const tab = this.tabs.get(id ?? this.selected ?? '') ?? this.open()

    if (this.selected !== tab.id) {
      this.selected = tab.id
      this.layout()
    }

    // A load a newer one replaces rejects; the tab says what failed.
    tab.view.webContents.loadURL(address).catch(() => undefined)

    return { id: tab.id, ok: true }
  }

  private navigate(command: 'back' | 'forward' | 'reload' | 'stop', id: string): BrowserAnswer {
    const tab = this.tabs.get(id)

    if (!tab) {
      return { ok: false, reason: 'unknown' }
    }

    const contents = tab.view.webContents

    if (command === 'back' && contents.navigationHistory.canGoBack()) {
      contents.navigationHistory.goBack()
    } else if (command === 'forward' && contents.navigationHistory.canGoForward()) {
      contents.navigationHistory.goForward()
    } else if (command === 'reload') {
      contents.reload()
    } else if (command === 'stop') {
      contents.stop()
    }

    return { ok: true }
  }

  /** A new browser tab, and the one on screen. `given` is a pop-up's `webContents`. */
  private open(given?: WebContents): Tab {
    this.counter += 1
    const view = new WebContentsView(
      given
        ? { webContents: given }
        : {
            webPreferences: {
              contextIsolation: true,
              nodeIntegration: false,
              partition: BROWSER_PARTITION,
              sandbox: true,
              webSecurity: true
            }
          }
    )
    const tab: Tab = { error: '', id: `tab-${String(this.counter)}`, view }
    const contents = view.webContents
    this.tabs.set(tab.id, tab)
    this.window.contentView.addChildView(view)
    this.selected = tab.id
    this.layout()
    this.guard(tab)
    this.keepAwake(contents)
    this.changed()

    return tab
  }

  /** Only web pages, and a pop-up as a tab; the tab follows its page's own events. */
  private guard(tab: Tab): void {
    const contents = tab.view.webContents
    const refuse = (event: { preventDefault: () => void }, url: string) => {
      if (!pageAllowed(url)) {
        event.preventDefault()
      }
    }

    contents.on('will-navigate', (event, url) => {
      refuse(event, url)
    })
    contents.on('will-redirect', (event, url) => {
      refuse(event, url)
    })
    contents.setWindowOpenHandler(({ url }) =>
      pageAllowed(url)
        ? {
            action: 'allow',
            // Electron's types leave out the `webContents` it puts in the options: the pop-up's own, which
            // the new view must be made from (a view of another one throws).
            createWindow: options => this.open((options as { webContents: WebContents }).webContents).view.webContents
          }
        : { action: 'deny' }
    )
    contents.on('did-start-navigation', (_event, _url, _inPlace, isMainFrame) => {
      if (isMainFrame) {
        tab.error = ''
      }
    })
    contents.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
      if (isMainFrame && code !== ABORTED) {
        tab.error = description
        this.changed()
      }
    })
    contents.on('render-process-gone', (_event, details) => {
      tab.error = details.reason
      this.changed()
    })
    contents.on('did-navigate', () => {
      this.keepAwake(contents)
    })

    for (const event of [
      'did-navigate',
      'did-navigate-in-page',
      'did-start-loading',
      'did-stop-loading',
      'page-title-updated'
    ] as const) {
      contents.on(event as 'did-navigate', () => {
        this.changed()
      })
    }

    contents.on('destroyed', () => {
      this.remove(tab, false)
    })
  }

  /** The three things a view needs to answer the agent while it is hidden (see the top). */
  private keepAwake(contents: WebContents): void {
    if (contents.isDestroyed()) {
      return
    }

    contents.setBackgroundThrottling(false)

    try {
      if (!contents.debugger.isAttached()) {
        contents.debugger.attach('1.3')
      }

      const send = (method: string, params: object) =>
        contents.debugger.sendCommand(method, params).catch((error: unknown) => {
          this.log(`The browser tab's ${method} failed: ${String(error)}`)
        })
      void send('Emulation.setFocusEmulationEnabled', { enabled: true })
      void send('Page.setWebLifecycleState', { state: 'active' })
    } catch (error) {
      this.log(`The browser tab's debugger did not attach: ${String(error)}`)
    }
  }

  private remove(tab: Tab, closeView: boolean, announce = true): void {
    if (!this.tabs.delete(tab.id)) {
      return
    }

    if (this.selected === tab.id) {
      const rest = [...this.tabs.keys()]
      this.selected = rest.length > 0 ? (rest[rest.length - 1] ?? null) : null
    }

    if (!this.window.isDestroyed()) {
      this.window.contentView.removeChildView(tab.view)
    }

    if (closeView && !tab.view.webContents.isDestroyed()) {
      tab.view.webContents.close()
    }

    if (announce) {
      this.layout()
      this.changed()
    }
  }

  /** The selected tab's view in the panel's place; every other one, and all while the panel is not
   * showing the browser, hidden (hidden views still answer the agent). */
  private layout(): void {
    for (const tab of this.tabs.values()) {
      const shown = this.rect !== null && tab.id === this.selected
      tab.view.setVisible(shown)

      if (shown && this.rect) {
        tab.view.setBounds(this.rect)
      }
    }
  }

  private stateOf(tab: Tab): BrowserTabState {
    const contents = tab.view.webContents
    const url = contents.getURL()

    return {
      canGoBack: contents.navigationHistory.canGoBack(),
      canGoForward: contents.navigationHistory.canGoForward(),
      error: tab.error,
      id: tab.id,
      loading: contents.isLoading(),
      title: contents.getTitle(),
      url: url === 'about:blank' ? '' : url
    }
  }

  /** Tell the window, once for a burst of events. */
  private changed(): void {
    if (this.tell !== null) {
      return
    }

    this.tell = setImmediate(() => {
      this.tell = null

      if (!this.window.isDestroyed() && !this.window.webContents.isDestroyed()) {
        this.window.webContents.send(BROWSER_CHANGED, this.snapshot())
      }
    })
  }
}

/** The window's channels to the browser (a command, its state, a look for local servers); each answers the window's own page alone. */
export function registerAppBrowser(ipc: IpcMain, current: () => AppBrowser | null): void {
  ipc.handle('atlas:browser:command', (event, value: unknown): BrowserAnswer => {
    const browser = current()

    if (!browser || event.sender !== browser.owner) {
      return { ok: false, reason: 'closed' }
    }

    const command = parseCommand(value)

    return command ? browser.run(command) : { ok: false, reason: 'invalid' }
  })
  ipc.handle('atlas:browser:state', (event): BrowserState => {
    const browser = current()

    return browser && event.sender === browser.owner ? browser.snapshot() : { selected: null, tabs: [] }
  })
  // The ports among the common ones, and the window's own, that answer as web servers here.
  ipc.handle('atlas:browser:servers', async (event, extra: unknown): Promise<number[]> => {
    const browser = current()
    const ports = parsePorts(extra)

    return browser && event.sender === browser.owner && ports ? lookForServers(portsToLook(ports)) : []
  })
}
