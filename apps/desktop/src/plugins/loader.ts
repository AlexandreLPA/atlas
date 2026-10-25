/**
 * Loading a plugin's screen (step 16m):
 *
 *   the server's `desktop/plugin.js` text -> its imports allowed (`@atlas/plugin-sdk`, `react`,
 *   `react/jsx-runtime`) and rewritten to shim blobs of the app's own modules -> a blob `import()`
 *   with a deadline -> its default export checked -> `register(ctx)`.
 *
 * Loading a plugin again takes away what it added before (its contributions, listeners, timers).
 * A screen that fails is said on the Plugins screen and never takes the app down.
 *
 * SECURITY: this is not a boundary. A screen runs in the app with all
 * the app's access; the isolation is of errors only. The user consents to that when turning the
 * plugin on (decided with the user on 21/10/2026: no sandbox). The import allowlist
 * is the one tripwire: a screen cannot pull more code from an address.
 */
import * as React from 'react'
import * as jsxRuntime from 'react/jsx-runtime'

import { current } from '@/store/app'

import { onPluginEvent } from './events'
import { $contributions, $pluginPage, contribute, type Contribution, screenFailed } from './registry'
import * as sdk from './sdk'

/** How long a screen's module may take to evaluate. */
export const IMPORT_SECONDS = 10

/** What the floating bar shows. */
export interface BarState {
  text: string
  tone: 'done' | 'error' | 'recording' | 'working'
}

/** Where a pasted text went. */
export type Pasted = 'app' | 'copied' | 'pasted'

/** A screen's default export. */
export interface AtlasScreen {
  /** The plugin's name, as its manifest names it. */
  id: string
  register: (ctx: ScreenContext) => void
}

/** What `register` is given: the contributions, and what it uses of Atlas, all taken away with it. */
export interface ScreenContext {
  /** The plugin's name. */
  readonly plugin: string
  /** Call its Python (`register_call`), declared under `atlas.calls`. */
  call: (name: string, params?: unknown) => Promise<unknown>
  /** Hear the server's events by type (`'*'` for all); taken away with the plugin. */
  onEvent: (type: string, listener: Parameters<typeof onPluginEvent>[1]) => () => void
  /** Anything else to undo when it is taken away. */
  onDispose: (undo: () => void) => void
  /** Add a contribution: a panel tab, a page, or a button of the conversation's box. */
  register: (contribution: Contribution) => () => void
  /** Timers taken away with it. */
  setInterval: (run: () => void, ms: number) => () => void
  setTimeout: (run: () => void, ms: number) => () => void
  /** The floating bar, above the taskbar on the screen the mouse is on, never taking the focus
   *  (version 3): `null` hides it. Hidden with the plugin. */
  bar: (state: BarState | null) => Promise<void>
  /** Put a text where the user is (version 3, `paste` declared under `atlas.desktop`): typed where
   *  the app's cursor is when its window has the focus (`app`), else pasted in the program in
   *  focus (`pasted`), or left on the clipboard when that program takes no keys (`copied`). */
  paste: (text: string) => Promise<Pasted>
  /** Hold a key heard in every program while the app is open (version 3, `shortcut` declared
   *  under `atlas.desktop`), as Electron names keys (`Capslock`, `Control+Shift+Space`); `run` on
   *  each press. Resolves `''`, or why the key cannot be held. The same `id` again replaces it,
   *  `''` lets go; let go with the plugin. */
  shortcut: (id: string, accelerator: string, run: () => void) => Promise<string>
  /** JSON kept in the app between openings, under keys of its own. */
  storage: {
    get: <T>(key: string, fallback: T) => T
    remove: (key: string) => void
    set: (key: string, value: unknown) => void
  }
}

const NAMESPACES = {
  __ATLAS_PLUGIN_SDK__: sdk,
  __ATLAS_REACT__: React,
  __ATLAS_REACT_JSX__: jsxRuntime
}

type Namespace = keyof typeof NAMESPACES

/** The specifiers a screen may import, and the app's module each stands for. */
const ALLOWED: Record<string, Namespace> = {
  '@atlas/plugin-sdk': '__ATLAS_PLUGIN_SDK__',
  react: '__ATLAS_REACT__',
  'react/jsx-runtime': '__ATLAS_REACT_JSX__'
}

/** A static `from '…'`, a side-effect `import '…'`, or a dynamic `import('…')`. */
const IMPORT = /(from\s*|import\s*\(\s*|import\s+)(["'])([^"']+)\2/g

/** The screen's text with its imports pointed at `urls`. Throws, naming it, on an import of
 *  anything else: a screen cannot pull code from an address or a file. */
export function rewriteImports(source: string, urls: Record<string, string>): string {
  return source.replace(IMPORT, (whole, head: string, quote: string, specifier: string) => {
    const url = urls[specifier]

    if (url === undefined) {
      throw new Error(`It imports ${specifier}; a screen imports only ${Object.keys(ALLOWED).join(', ')}.`)
    }

    return `${head}${quote}${url}${quote}`
  })
}

let shims: null | Record<string, string> = null

/** A name a module can export under `export const { … }`. */
const EXPORTABLE = /^[A-Za-z_$][\w$]*$/

/** The module that stands for the app's `global`: its default export, then every name it can export. */
function shimSource(global: Namespace): string {
  const exported = Object.keys(NAMESPACES[global]).filter(key => key !== 'default' && EXPORTABLE.test(key))
  const lines = [`const m = globalThis.${global};`, 'export default m.default ?? m;']

  if (exported.length > 0) {
    lines.push(`export const { ${exported.join(', ')} } = m;`)
  }

  return `${lines.join('\n')}\n`
}

/** The shim modules, made once: each re-exports one of the app's modules from a global. */
function shimUrls(): Record<string, string> {
  if (shims === null) {
    Object.assign(globalThis, NAMESPACES)
    const made: Record<string, string> = {}

    for (const [specifier, global] of Object.entries(ALLOWED)) {
      const blob = new Blob([shimSource(global)], { type: 'text/javascript' })
      made[specifier] = URL.createObjectURL(blob)
    }

    shims = made
  }

  return shims
}

/** The plugins loaded now: what each added, to take away, the text it was loaded from, and its
 *  desktop powers. */
const loaded = new Map<string, { desktop: readonly string[]; source: string; undo: (() => void)[] }>()

/** The shortcuts the screens hold, by `<plugin>:<id>`: what each press runs. */
const pressedRuns = new Map<string, () => void>()
let hearingPresses = false

/** The preload's desktop powers; absent where no preload runs (the unit tests). */
function desktopBridge(): DesktopBridge | undefined {
  return (window as { atlas?: Partial<AtlasBridge> }).atlas?.desktop
}

/** Hear the main process's presses once, for every screen. */
function hearPresses(): void {
  const found = desktopBridge()

  if (!hearingPresses && found) {
    hearingPresses = true
    found.onPressed(key => {
      pressedRuns.get(key)?.()
    })
  }
}

export function loadedScreens(): string[] {
  return [...loaded.keys()]
}

/** Load a plugin's screen, taking away what an earlier load of it added. Its failure is kept for
 *  the Plugins screen, and logged. */
export async function loadScreen(plugin: string, source: string, desktop: readonly string[] = []): Promise<boolean> {
  // A screen loaded again (step 16n, saved by its author) keeps its page on screen: a plugin
  // reloads in place.
  const page = $pluginPage.get()
  unloadScreen(plugin)
  const undo: (() => void)[] = []
  loaded.set(plugin, { desktop, source, undo })

  try {
    const url = URL.createObjectURL(new Blob([rewriteImports(source, shimUrls())], { type: 'text/javascript' }))
    let module: { default?: unknown }

    try {
      module = await withDeadline(import(/* @vite-ignore */ url) as Promise<{ default?: unknown }>)
    } finally {
      URL.revokeObjectURL(url)
    }

    const screen = module.default as Partial<AtlasScreen> | undefined

    if (!screen || typeof screen.register !== 'function') {
      throw new Error('Its desktop/plugin.js has no default export with register(ctx).')
    }

    if (screen.id !== plugin) {
      throw new Error(`Its screen says id ${String(screen.id)}; it must be ${plugin}, the plugin's name.`)
    }

    screen.register(context(plugin, undo, desktop))
    screenFailed(plugin, null)

    if (page !== null && $pluginPage.get() === null && $contributions.get().some(item => item.key === page)) {
      $pluginPage.set(page)
    }

    return true
  } catch (error) {
    unloadScreen(plugin)
    const why = error instanceof Error ? error.message : String(error)
    screenFailed(plugin, why)
    console.error(`The screen of ${plugin} did not load.`, error)

    return false
  }
}

/** Take away everything a plugin's screen added. */
export function unloadScreen(plugin: string): void {
  const found = loaded.get(plugin)
  loaded.delete(plugin)

  for (const undo of found?.undo.splice(0).reverse() ?? []) {
    try {
      undo()
    } catch (error) {
      console.error(`Taking away the screen of ${plugin} failed in part.`, error)
    }
  }
}

/** The desktop powers a plugin's screen was loaded with, joined, to tell whether they changed. */
export function loadedPowers(plugin: string): string | undefined {
  return loaded.get(plugin)?.desktop.join(',')
}

/** The source a plugin's screen was loaded from, to tell whether it changed. */
export function loadedSource(plugin: string): string | undefined {
  return loaded.get(plugin)?.source
}

function context(plugin: string, undo: (() => void)[], desktop: readonly string[]): ScreenContext {
  const keep = (taken: () => void) => {
    undo.push(taken)

    return taken
  }
  /** The bridge's desktop powers, when the screen declared `power`. */
  const bridge = (power: string): DesktopBridge => {
    if (!desktop.includes(power)) {
      throw new Error(`${plugin} declares no desktop power ${power}: name it under atlas.desktop (atlas.api 23).`)
    }

    const found = desktopBridge()

    if (!found) {
      throw new Error('This app runs no desktop powers.')
    }

    return found
  }
  let barShown = false
  const prefix = `atlas.plugin.${plugin}.`

  return {
    call: async (name, params) => {
      const { result } = await current().client.call('plugin.call', { name, params: params ?? null, plugin })

      return result
    },
    onDispose: taken => {
      undo.push(taken)
    },
    onEvent: (type, listener) => keep(onPluginEvent(type, listener)),
    bar: async state => {
      const found = desktopBridge()

      if (!found) {
        return
      }

      if (state && !barShown) {
        barShown = true
        undo.push(() => {
          barShown = false
          void found.bar(null)
        })
      }

      await found.bar(state ? { text: state.text, tone: state.tone } : null)
    },
    paste: async text => bridge('paste').paste(text),
    plugin,
    register: contribution => keep(contribute(plugin, contribution)),
    shortcut: async (id, accelerator, run) => {
      const desktop = bridge('shortcut')
      const key = `${plugin}:${id}`
      hearPresses()
      const why = await desktop.shortcut(key, accelerator)

      if (why || !accelerator.trim()) {
        pressedRuns.delete(key)

        return why
      }

      if (!pressedRuns.has(key)) {
        undo.push(() => {
          pressedRuns.delete(key)
          void desktop.shortcut(key, '')
        })
      }

      pressedRuns.set(key, run)

      return ''
    },
    setInterval: (run, ms) => {
      const id = window.setInterval(run, ms)

      return keep(() => {
        window.clearInterval(id)
      })
    },
    setTimeout: (run, ms) => {
      const id = window.setTimeout(run, ms)

      return keep(() => {
        window.clearTimeout(id)
      })
    },
    storage: {
      get: <T>(key: string, fallback: T): T => {
        const kept = window.localStorage.getItem(prefix + key)

        if (kept === null) {
          return fallback
        }

        try {
          return JSON.parse(kept) as T
        } catch {
          return fallback
        }
      },
      remove: key => {
        window.localStorage.removeItem(prefix + key)
      },
      set: (key, value) => {
        window.localStorage.setItem(prefix + key, JSON.stringify(value))
      }
    }
  }
}

/** `loading`, failed if it has not settled within `IMPORT_SECONDS`. */
function withDeadline<Loaded>(loading: Promise<Loaded>): Promise<Loaded> {
  return new Promise<Loaded>((succeed, fail) => {
    const deadline = window.setTimeout(() => {
      fail(new Error(`Its module took more than ${String(IMPORT_SECONDS)} s to load.`))
    }, IMPORT_SECONDS * 1000)
    const stopClock = () => {
      window.clearTimeout(deadline)
    }

    loading.then(
      loaded => {
        stopClock()
        succeed(loaded)
      },
      (thrown: unknown) => {
        stopClock()
        fail(thrown instanceof Error ? thrown : new Error(String(thrown)))
      }
    )
  })
}
