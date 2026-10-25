/**
 * What a plugin's screen does outside the window (step 17b, screen version 3), for the voice
 * plugin's dictation anywhere: each declared in the plugin's manifest (`atlas.desktop`) and shown
 * at consent.
 *
 * - **A global shortcut** (`shortcut`): a key heard while any program has the focus, through
 *   Electron's own `globalShortcut`, so a key press is heard and a key release is not. Caps Lock
 *   as the shortcut still turns capitals on and off as the system reads it, so it is pressed once
 *   more for the user, with the shortcut let go meanwhile (found on 25/10/2026: the press that
 *   records also flipped the capitals).
 * - **Pasting into the program in focus** (`paste`): the text goes on the clipboard, Ctrl+V (Cmd+V
 *   on a Mac) is pressed for the user, and what the clipboard held is put back once the program
 *   has read it, unless something else was copied meanwhile. With the app's own window in focus
 *   the text is typed where its cursor is, with no clipboard. A program that does not take the
 *   keys (on Windows, one running as administrator, which refuses an ordinary program's keys)
 *   leaves the text on the clipboard, and the screen says so.
 * - **A bar** that shows what goes on (recording, writing it down) above the taskbar, on the screen
 *   the mouse is on, never taking the focus from the program the text is for.
 *
 * The keys are pressed by a helper of the system's own: on Windows a PowerShell kept running, which
 * asks whether the window in focus runs above the app's rights and sends the keys (`SendInput`);
 * on a Mac `osascript`; on Linux `xdotool`, or `wtype` under Wayland. None is a library the app
 * ships.
 */
import { type ChildProcessWithoutNullStreams, execFile, spawn } from 'node:child_process'

import type * as Electron from 'electron'
import type { BrowserWindow, IpcMain } from 'electron'

/** Caps Lock, as Electron names it. */
export const CAPS_LOCK = 'Capslock'
/** How long the program in focus is given to read the clipboard before it is put back. */
export const RESTORE_MS = 600
/** A shortcut heard again sooner than this is the key held down, repeating. */
export const REPEAT_MS = 300

/** Where a text went: typed in the app's window, pasted in another program, or left on the
 *  clipboard for the user to paste. */
export type Pasted = 'app' | 'copied' | 'pasted'

/** The keys pressed for the user. */
export interface Keys {
  /** Press paste in the program in focus: `blocked` when it does not take the keys. */
  paste: () => Promise<'blocked' | 'pasted'>
  /** Press Caps Lock once, to put the capitals back as they were. */
  tapCapsLock: () => Promise<void>
  close: () => void
}

/** Electron's `globalShortcut`, as far as this uses it. */
export interface ShortcutSystem {
  register: (accelerator: string, run: () => void) => boolean
  unregister: (accelerator: string) => void
}

/** The screens' shortcuts, by `<plugin>:<id>`, each told to the window when pressed. */
export class Shortcuts {
  private readonly held = new Map<string, string>()
  private readonly last = new Map<string, number>()

  constructor(
    private readonly system: ShortcutSystem,
    private readonly keys: Keys | null,
    private readonly pressed: (key: string) => void,
    private readonly now: () => number = Date.now
  ) {}

  /** Hold `accelerator` for `key`, letting go of what it held; `''` lets go only. Returns `''`,
   *  or why the key cannot be held. */
  set(key: string, accelerator: string): string {
    const before = this.held.get(key)

    if (before !== undefined) {
      this.system.unregister(before)
      this.held.delete(key)
    }

    const wanted = accelerator.trim()

    if (!wanted) {
      return ''
    }

    if ([...this.held.values()].includes(wanted)) {
      return `${wanted} is another plugin's shortcut.`
    }

    let held: boolean

    try {
      held = this.system.register(wanted, () => {
        this.heard(key, wanted)
      })
    } catch {
      return `${wanted} is not a key the system knows.`
    }

    if (!held) {
      return `${wanted} is taken by another program.`
    }

    this.held.set(key, wanted)

    return ''
  }

  /** Let go of every shortcut. */
  clear(): void {
    for (const key of [...this.held.keys()]) {
      this.set(key, '')
    }
  }

  private heard(key: string, accelerator: string): void {
    const at = this.now()

    if (at - (this.last.get(key) ?? -Infinity) < REPEAT_MS) {
      return
    }

    this.last.set(key, at)
    this.pressed(key)

    if (accelerator === CAPS_LOCK && this.keys) {
      // The press flipped the capitals: press it once more, the shortcut let go so that this
      // press is not heard as the user's.
      this.system.unregister(accelerator)
      void this.keys
        .tapCapsLock()
        .catch(() => undefined)
        .finally(() => {
          if (this.held.get(key) === accelerator) {
            this.system.register(accelerator, () => {
              this.heard(key, accelerator)
            })
          }
        })
    }
  }
}

/** Electron's clipboard, as far as this uses it. */
export interface ClipboardLike {
  availableFormats: () => string[]
  readHTML: () => string
  readImage: () => { isEmpty: () => boolean }
  readRTF: () => string
  readText: () => string
  write: (data: { html?: string; image?: never; rtf?: string; text?: string }) => void
  writeText: (text: string) => void
}

/** Put `text` where the user is: typed in the app's window when it has the focus (`typeInApp`
 *  returns whether it did), else pasted in the program in focus, the clipboard put back after. */
export async function pasteText(
  text: string,
  {
    clipboard,
    keys,
    typeInApp,
    wait = ms => new Promise(resolve => setTimeout(resolve, ms))
  }: {
    clipboard: ClipboardLike
    keys: Keys | null
    typeInApp: (text: string) => boolean
    wait?: (ms: number) => Promise<void>
  }
): Promise<Pasted> {
  if (typeInApp(text)) {
    return 'app'
  }

  const kept = keep(clipboard)
  clipboard.writeText(text)

  if (!keys || (await keys.paste().catch(() => 'blocked' as const)) !== 'pasted') {
    // The text stays on the clipboard, for the user to paste.
    return 'copied'
  }

  await wait(RESTORE_MS)

  if (clipboard.readText() === text) {
    kept()
  }

  return 'pasted'
}

/** What puts the clipboard back as it is now: its text, HTML, rich text and image. */
function keep(clipboard: ClipboardLike): () => void {
  const formats = clipboard.availableFormats()
  const image = clipboard.readImage()
  const data = {
    html: formats.some(format => format.includes('html')) ? clipboard.readHTML() : undefined,
    image: image.isEmpty() ? undefined : (image as never),
    rtf: formats.some(format => format.includes('rtf')) ? clipboard.readRTF() : undefined,
    text: clipboard.readText()
  }

  return () => {
    clipboard.write(Object.fromEntries(Object.entries(data).filter(([, value]) => value !== undefined)))
  }
}

/** The system's keys, or `null` where there is no way to press them. */
export function systemKeys(platform: NodeJS.Platform = process.platform): Keys | null {
  if (platform === 'win32') {
    return new WindowsKeys()
  }

  if (platform === 'darwin') {
    return {
      close: () => undefined,
      paste: async () =>
        (await run('osascript', ['-e', 'tell application "System Events" to keystroke "v" using command down']))
          ? 'pasted'
          : 'blocked',
      tapCapsLock: () => Promise.resolve()
    }
  }

  return {
    close: () => undefined,
    paste: async () =>
      (await run('xdotool', ['key', '--clearmodifiers', 'ctrl+v'])) ||
      (await run('wtype', ['-M', 'ctrl', 'v', '-m', 'ctrl']))
        ? 'pasted'
        : 'blocked',
    tapCapsLock: async () => {
      await run('xdotool', ['key', 'Caps_Lock'])
    }
  }
}

/** Whether a command ran and ended well. */
function run(command: string, args: string[]): Promise<boolean> {
  return new Promise(resolve => {
    execFile(command, args, { timeout: 5_000 }, error => {
      resolve(!error)
    })
  })
}

/**
 * The Windows helper: a PowerShell with a few calls to the system compiled in once, kept running
 * so that a paste does not wait for PowerShell to start. One line in, one line out.
 *
 * `paste` presses Ctrl+V unless the window in focus belongs to a process above this one's rights:
 * one running as administrator refuses an ordinary program's keys without a word, so the text
 * would vanish. A process whose rights cannot be read is taken as above.
 */
const WINDOWS_HELPER = String.raw`
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AtlasKeys {
  [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int dx, dy; public uint data, flags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort vk, scan; public uint flags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Explicit)] struct UNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
  [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION u; }

  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
  [DllImport("user32.dll")] static extern uint SendInput(uint count, INPUT[] inputs, int size);
  [DllImport("kernel32.dll")] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
  [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [DllImport("advapi32.dll")] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
  [DllImport("advapi32.dll")] static extern bool GetTokenInformation(IntPtr token, int kind, out int value, int size, out int written);

  static INPUT Key(ushort vk, bool up) {
    var input = new INPUT { type = 1 };
    input.u.ki.vk = vk;
    input.u.ki.flags = up ? 2u : 0u;
    return input;
  }

  static bool Send(INPUT[] keys) { return SendInput((uint)keys.Length, keys, Marshal.SizeOf(typeof(INPUT))) == keys.Length; }

  // 1 elevated, 0 not, -1 its rights cannot be read.
  static int Elevated(IntPtr process) {
    IntPtr token;
    if (!OpenProcessToken(process, 8, out token)) return -1;
    int value, written;
    bool read = GetTokenInformation(token, 20, out value, 4, out written);
    CloseHandle(token);
    return read ? (value != 0 ? 1 : 0) : -1;
  }

  public static string Paste() {
    if (Elevated(GetCurrentProcess()) != 1) {
      uint pid;
      GetWindowThreadProcessId(GetForegroundWindow(), out pid);
      IntPtr process = OpenProcess(0x1000, false, pid);
      int target = process == IntPtr.Zero ? -1 : Elevated(process);
      if (process != IntPtr.Zero) CloseHandle(process);
      if (target != 0) return "blocked";
    }
    return Send(new[] { Key(0x11, false), Key(0x56, false), Key(0x56, true), Key(0x11, true) }) ? "pasted" : "blocked";
  }

  public static string TapCapsLock() { return Send(new[] { Key(0x14, false), Key(0x14, true) }) ? "tapped" : "failed"; }
}
'@
[Console]::Out.WriteLine('ready')
while ($null -ne ($line = [Console]::In.ReadLine())) {
  switch ($line) {
    'paste' { [Console]::Out.WriteLine([AtlasKeys]::Paste()) }
    'caps' { [Console]::Out.WriteLine([AtlasKeys]::TapCapsLock()) }
    default { [Console]::Out.WriteLine('unknown') }
  }
}
`

/** The PowerShell helper, started on first use and again after it ends. */
class WindowsKeys implements Keys {
  private child: ChildProcessWithoutNullStreams | null = null
  private lines: string[] = []
  private waiting: ((line: null | string) => void)[] = []
  private queue: Promise<unknown> = Promise.resolve()

  paste(): Promise<'blocked' | 'pasted'> {
    return this.ask('paste').then(line => (line === 'pasted' ? 'pasted' : 'blocked'))
  }

  async tapCapsLock(): Promise<void> {
    await this.ask('caps')
  }

  close(): void {
    this.child?.kill()
    this.child = null
  }

  /** One request at a time, each answered by one line; `null` when the helper ended. */
  private ask(request: string): Promise<null | string> {
    const asked = this.queue.then(async () => {
      if (!this.child) {
        this.start()

        if ((await this.next()) !== 'ready') {
          return null
        }
      }

      this.child?.stdin.write(`${request}\n`)

      return this.next()
    })
    this.queue = asked.catch(() => undefined)

    return asked
  }

  private start(): void {
    const encoded = Buffer.from(WINDOWS_HELPER, 'utf16le').toString('base64')
    const child = spawn(
      'powershell.exe',
      ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
      { windowsHide: true }
    )
    this.child = child
    this.lines = []
    let partial = ''
    child.stdout.on('data', (data: Buffer) => {
      partial += data.toString('utf8')
      const parts = partial.split(/\r?\n/)
      partial = parts.pop() ?? ''

      for (const line of parts.filter(Boolean)) {
        const waiter = this.waiting.shift()

        if (waiter) {
          waiter(line.trim())
        } else {
          this.lines.push(line.trim())
        }
      }
    })
    child.on('exit', () => {
      if (this.child === child) {
        this.child = null
      }

      for (const waiter of this.waiting.splice(0)) {
        waiter(null)
      }
    })
    child.on('error', () => undefined)
  }

  private next(): Promise<null | string> {
    const line = this.lines.shift()

    if (line !== undefined) {
      return Promise.resolve(line)
    }

    if (!this.child) {
      return Promise.resolve(null)
    }

    return new Promise(resolve => {
      this.waiting.push(resolve)
    })
  }
}

/** What the bar shows: its words, and how it looks. */
export interface BarState {
  text: string
  tone: 'done' | 'error' | 'recording' | 'working'
}

/** The bar's page: no script of its own beyond showing what it is given. */
const BAR_PAGE = `<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>
html,body{margin:0;height:100%;background:transparent;overflow:hidden;font:13px system-ui,sans-serif}
#bar{position:absolute;inset:4px;display:flex;align-items:center;gap:10px;padding:0 16px;border-radius:999px;
background:rgba(23,23,23,.92);color:#f5f5f5;box-shadow:0 4px 16px rgba(0,0,0,.35);white-space:nowrap}
#dot{width:10px;height:10px;border-radius:50%;flex:none;background:#a3a3a3}
#bar.recording #dot{background:#f43f5e;animation:pulse 1s infinite}
#bar.working #dot{background:#38bdf8;animation:pulse 1s infinite}
#bar.done #dot{background:#22c55e}
#bar.error #dot{background:#f59e0b}
#text{overflow:hidden;text-overflow:ellipsis}
@keyframes pulse{50%{opacity:.35}}
</style></head><body><div id="bar"><span id="dot"></span><span id="text"></span></div>
<script>window.show=function(s){document.getElementById('bar').className=s.tone;document.getElementById('text').textContent=s.text}</script>
</body></html>`

/** The bar's size, in the screen's points. */
const BAR = { height: 44, width: 380 }

/** The floating bar, made on first use; Electron's own, so not in the unit tests. */
export class Bar {
  private window: BrowserWindow | null = null
  private loading: null | Promise<unknown> = null

  constructor(private readonly electron: Pick<typeof Electron, 'BrowserWindow' | 'screen'>) {}

  async show(state: BarState): Promise<void> {
    const bar = this.made()
    await this.loading
    const { workArea } = this.electron.screen.getDisplayNearestPoint(this.electron.screen.getCursorScreenPoint())
    bar.setBounds({
      height: BAR.height,
      width: BAR.width,
      x: Math.round(workArea.x + (workArea.width - BAR.width) / 2),
      y: Math.round(workArea.y + workArea.height - BAR.height - 24)
    })
    await bar.webContents.executeJavaScript(`window.show(${JSON.stringify(state)})`)

    if (!bar.isVisible()) {
      bar.showInactive()
    }
  }

  hide(): void {
    this.window?.hide()
  }

  close(): void {
    this.window?.destroy()
    this.window = null
  }

  private made(): BrowserWindow {
    if (this.window && !this.window.isDestroyed()) {
      return this.window
    }

    const bar = new this.electron.BrowserWindow({
      alwaysOnTop: true,
      focusable: false,
      frame: false,
      hasShadow: false,
      height: BAR.height,
      resizable: false,
      show: false,
      skipTaskbar: true,
      transparent: true,
      webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
      width: BAR.width
    })
    bar.setAlwaysOnTop(true, 'screen-saver')
    bar.setIgnoreMouseEvents(true)
    bar.setVisibleOnAllWorkspaces(true)
    this.window = bar
    this.loading = bar.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(BAR_PAGE)}`)

    return bar
  }
}

/** The powers, as the main process gives them to the window. */
export interface DesktopPowers {
  bar: { hide: () => void; show: (state: BarState) => Promise<void> }
  keys: Keys | null
  shortcuts: ShortcutSystem
}

/** Answer the window's asks: a shortcut held or let go, a text pasted, the bar shown or hidden.
 *  Returns what lets everything go, as the app quits. */
export function registerDesktopPowers(
  ipc: IpcMain,
  window: () => BrowserWindow | null,
  powers: DesktopPowers,
  clipboard: ClipboardLike,
  focused: () => boolean = () => window()?.isFocused() ?? false
): () => void {
  const shortcuts = new Shortcuts(powers.shortcuts, powers.keys, key => {
    window()?.webContents.send('atlas:desktop:pressed', key)
  })
  ipc.handle('atlas:desktop:shortcut', (_event, key: unknown, accelerator: unknown) =>
    shortcuts.set(String(key), typeof accelerator === 'string' ? accelerator : '')
  )
  ipc.handle('atlas:desktop:paste', (_event, text: unknown) =>
    pasteText(typeof text === 'string' ? text : '', {
      clipboard,
      keys: powers.keys,
      typeInApp: words => {
        const shown = window()

        if (!shown || !focused()) {
          return false
        }

        void shown.webContents.insertText(words)

        return true
      }
    })
  )
  ipc.handle('atlas:desktop:bar', async (_event, state: unknown) => {
    if (state && typeof state === 'object' && 'text' in state) {
      await powers.bar.show(state as BarState)
    } else {
      powers.bar.hide()
    }
  })

  return () => {
    shortcuts.clear()
    powers.keys?.close()
  }
}

/** What the end-to-end tests see and do: the shortcuts held, pressed as the test says; the texts
 *  pasted, with no key pressed; the bar's states; and whether the app's window counts as in focus
 *  (`null`: as it is). */
export interface DesktopProbe {
  appFocused: boolean | null
  bars: (BarState | null)[]
  held: Map<string, () => void>
  pasted: string[]
  press: (accelerator: string) => boolean
}

/** The powers under the end-to-end tests: nothing reaches the system. */
export function e2ePowers(clipboard: ClipboardLike): { powers: DesktopPowers; probe: DesktopProbe } {
  const held = new Map<string, () => void>()
  const probe: DesktopProbe = {
    appFocused: null,
    bars: [],
    held,
    pasted: [],
    press: accelerator => {
      const run = held.get(accelerator)
      run?.()

      return run !== undefined
    }
  }

  return {
    powers: {
      bar: {
        hide: () => {
          probe.bars.push(null)
        },
        show: state => {
          probe.bars.push(state)

          return Promise.resolve()
        }
      },
      keys: {
        close: () => undefined,
        paste: () => {
          probe.pasted.push(clipboard.readText())

          return Promise.resolve('pasted')
        },
        tapCapsLock: () => Promise.resolve()
      },
      shortcuts: {
        register: (accelerator, run) => {
          if (held.has(accelerator)) {
            return false
          }

          held.set(accelerator, run)

          return true
        },
        unregister: accelerator => {
          held.delete(accelerator)
        }
      }
    },
    probe
  }
}
