/**
 * The app's update (asked for by the user on 18/10/2026): a button that shows when `main` has
 * commits the installation lacks, and an update that closes the app, updates it and opens it again
 * by itself, where the user had to close it, run `atlas update` in a terminal and open it again:
 *
 * - **Python looks, the app carries**: `python -m atlas.installation check` fetches and counts. The
 *   app looks when it opens, every hour, and when the window comes back after an hour, since
 *   Atlas's `main` moves several times a day.
 * - **The update is handed off** (`python -m atlas.installation handoff`, which lives in the
 *   checkout, so each update brings the code of the next): the app starts it, waits until its
 *   marker names it (it took), and closes; the hand-off waits for the app to be gone, updates,
 *   writes how it went and opens the app again, which says so once.
 * - **On Windows it starts through `cmd /d /s /c start "" /b`**, not detached and hidden: a console
 *   program started detached gets no console, and each program it starts would open a window of its
 *   own; started so, it keeps a hidden console its programs share, and outlives the app (libuv's
 *   job lets a grandchild leave).
 * - **An app opened while an update runs** says so and closes: the update replaces what the app
 *   runs from, and opens the app itself when it ends.
 */
import { execFile, spawn } from 'node:child_process'
import { existsSync, readFileSync, rmSync } from 'node:fs'
import path from 'node:path'
import { promisify } from 'node:util'

/** What `python -m atlas.installation check` says (`atlas.installation.UpdateStatus`). */
export interface UpdateStatus {
  available: boolean
  behind: number
  commits: string[]
  /** Each commit's `Notes-pt-BR` trailer, its subject in Portuguese, or "" when it has none. */
  commits_pt_br: string[]
  current: string
  dirty: boolean
  error: string
  /** `native` here; a server's host updater says `compose` (src/store/updates.ts). */
  kind?: string
  reason: string
  supported: boolean
  target: string
  unverified?: string
}

/** How the update the app handed off ended (`atlas.installation.HandedOff`). */
export interface HandedOff {
  after: string
  before: string
  commits: string[]
  ended_at: string
  error: string
  log: string
  ok: boolean
  reopen: string
}

/** Where an update keeps what it says, in the app's own folder. */
export interface UpdateFiles {
  log: string
  marker: string
  result: string
}

/** How `atlas.installation` is run: the installation's Python, on its checkout. */
export interface UpdateRun {
  cwd: string
  env: NodeJS.ProcessEnv
  /** What runs the module: `-m atlas.installation`, or the end-to-end tests' stand-in. */
  module: string[]
  python: string
}

/** How long a look may take: a fetch on a slow network (the Python side gives up at 120 s). */
export const CHECK_TIMEOUT_MS = 180_000
/** How long the hand-off has to say it took. */
export const HANDSHAKE_MS = 20_000
/** How often the app looks while it is open, and how long a window away before it looks again. */
export const CHECK_EVERY_MS = 60 * 60_000
/** An update's marker older than this is one that died: the app opens. */
export const STALE_MARKER_MS = 30 * 60_000

const execFileAsync = promisify(execFile)

export function updateFiles(userData: string): UpdateFiles {
  return {
    log: path.join(userData, 'update.log'),
    marker: path.join(userData, 'update-running.json'),
    result: path.join(userData, 'update-result.json')
  }
}

/** The installation's own Python, whose environment holds Atlas installed from its checkout. */
export function installationPython(root: string, platform: NodeJS.Platform = process.platform): string {
  return platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'python.exe')
    : path.join(root, '.venv', 'bin', 'python')
}

const NO_UPDATE: UpdateStatus = {
  available: false,
  behind: 0,
  commits: [],
  commits_pt_br: [],
  current: '',
  dirty: false,
  error: '',
  reason: '',
  supported: false,
  target: ''
}

/** A look's answer, read; one that cannot be read is a failed look, which offers nothing. */
export function parseStatus(text: string): UpdateStatus {
  try {
    const found = JSON.parse(text) as Partial<UpdateStatus> | null

    if (found === null || typeof found !== 'object' || typeof found.supported !== 'boolean') {
      throw new Error('it is not a status')
    }

    return {
      ...NO_UPDATE,
      ...found,
      available: found.available === true,
      commits: Array.isArray(found.commits) ? found.commits.map(String) : [],
      commits_pt_br: Array.isArray(found.commits_pt_br) ? found.commits_pt_br.map(String) : []
    }
  } catch (error) {
    return { ...NO_UPDATE, error: `The look for an update said nothing readable (${String(error)}).`, supported: true }
  }
}

/** Ask Python whether `main` has commits the installation lacks. */
export async function checkUpdate(run: UpdateRun): Promise<UpdateStatus> {
  try {
    const { stdout } = await execFileAsync(run.python, [...run.module, 'check'], {
      cwd: run.cwd,
      encoding: 'utf8',
      env: run.env,
      maxBuffer: 1024 * 1024,
      timeout: CHECK_TIMEOUT_MS,
      windowsHide: true
    })

    return parseStatus(stdout)
  } catch (error) {
    const missing = (error as NodeJS.ErrnoException).code === 'ENOENT'

    // No environment to run it from (a development checkout never synced) is no installation.
    return missing ? { ...NO_UPDATE, reason: 'development' } : { ...NO_UPDATE, error: String(error), supported: true }
  }
}

/**
 * What this launch was given that is the user's, or the launcher's, not Chromium's or a debugger's:
 * the update opens the app again with it. It keeps `--user-data-dir` and `--lang`: here they are
 * how the app was opened (a profile apart, a language), never something Chromium added. The app's
 * folder is the update's to give, and the end-to-end flag is the tests' alone.
 */
const NOT_GIVEN_AGAIN = [
  '--type=',
  '--enable-features=',
  '--disable-features=',
  '--field-trial-handle=',
  '--enable-logging',
  '--log-file=',
  '--disable-gpu-sandbox',
  '--inspect',
  '--remote-debugging-port=',
  '--atlas-e2e'
]

export function reopenFlags(given: readonly string[]): string[] {
  return given.filter(
    flag =>
      flag.startsWith('--') &&
      !NOT_GIVEN_AGAIN.some(prefix =>
        prefix.endsWith('=') ? flag.startsWith(prefix) : flag === prefix || flag.startsWith(`${prefix}=`)
      )
  )
}

/** The hand-off's command line, per system (the recipe on Windows, above). */
export function handOffCommand(
  run: UpdateRun,
  files: UpdateFiles,
  appPid: number,
  platform: NodeJS.Platform = process.platform,
  given: readonly string[] = []
): { args: string[]; command: string; detached: boolean } {
  const args = [
    ...run.module,
    'handoff',
    '--app-pid',
    String(appPid),
    '--marker',
    files.marker,
    '--result',
    files.result,
    '--log',
    files.log,
    // Its `=` keeps a flag that starts with `--` from being read as one of the hand-off's own.
    ...reopenFlags(given).map(flag => `--reopen-flag=${flag}`)
  ]

  if (platform !== 'win32') {
    return { args, command: run.python, detached: true }
  }

  // cmd expands `%` inside quotes too, and ends a line at a line break: a path holding one could
  // run something else, so none is passed.
  if ([run.python, ...args].some(value => /[%"\r\n]/.test(value))) {
    throw new Error('The update cannot start: a path of the app holds % or a quote.')
  }

  return { args: ['/d', '/s', '/c', 'start', '', '/b', run.python, ...args], command: 'cmd.exe', detached: false }
}

/** The process an update's marker names, or `null` when none runs (no marker, an old one). */
export function markerPid(text: null | string, now: number): null | number {
  if (text === null) {
    return null
  }

  try {
    const found = JSON.parse(text) as { pid?: unknown; started_at?: unknown }
    const started = typeof found.started_at === 'string' ? Date.parse(found.started_at) : NaN

    if (typeof found.pid !== 'number' || !Number.isFinite(started) || now - started > STALE_MARKER_MS) {
      return null
    }

    return found.pid
  } catch {
    return null
  }
}

function read(file: string): null | string {
  try {
    return readFileSync(file, 'utf8')
  } catch {
    return null
  }
}

/** Whether a process is alive; Node's signal 0 only asks, on Windows too. */
export function alive(pid: number): boolean {
  try {
    process.kill(pid, 0)
  } catch (thrown) {
    // EPERM: the process is there, it only belongs to someone else.
    return (thrown as NodeJS.ErrnoException).code === 'EPERM'
  }

  return true
}

/** The update that runs now, by its marker: an app opened meanwhile closes. */
export function updateRunning(files: UpdateFiles, now = Date.now()): boolean {
  const pid = markerPid(read(files.marker), now)

  return pid !== null && alive(pid)
}

/** Start the hand-off and wait until it says it took; the app closes after, and opens again with
 * the flags it was `given`. Throws when it does not take in time: the app stays open, and says
 * why. */
export async function handOff(
  run: UpdateRun,
  files: UpdateFiles,
  appPid: number,
  { given = [], waitMs = HANDSHAKE_MS }: { given?: readonly string[]; waitMs?: number } = {}
) {
  if (!existsSync(run.python)) {
    // Through cmd's start, a missing program would only be seen as a hand-off that never took.
    throw new Error(`The installation's Python is missing (${run.python}); run the installer to repair it.`)
  }

  rmSync(files.marker, { force: true })
  rmSync(files.result, { force: true })
  const { args, command, detached } = handOffCommand(run, files, appPid, process.platform, given)
  const child = spawn(command, args, { cwd: run.cwd, detached, env: run.env, stdio: 'ignore', windowsHide: true })
  const failed = new Promise<never>((_, reject) => {
    child.once('error', error => {
      reject(new Error(`The update could not start: ${error.message}`))
    })
  })
  child.unref()
  const giveUpAt = Date.now() + waitMs
  const pause = () => new Promise(wake => setTimeout(wake, 100))

  const took = (async () => {
    for (;;) {
      if (markerPid(read(files.marker), Date.now()) !== null) {
        return
      }

      if (Date.now() > giveUpAt) {
        throw new Error(`The update did not start in ${String(waitMs / 1000)} s; the app stays open.`)
      }

      await pause()
    }
  })()

  await Promise.race([took, failed])
}

/** How the last update ended, once: the app reads it as it opens, and it is gone after. */
export function takeResult(files: UpdateFiles): HandedOff | null {
  const text = read(files.result)

  if (text === null) {
    return null
  }

  rmSync(files.result, { force: true })

  try {
    const found = JSON.parse(text) as Record<string, unknown>
    const said = (key: string): string => (typeof found[key] === 'string' ? found[key] : '')
    const commits = found.commits

    return {
      after: said('after'),
      before: said('before'),
      commits: Array.isArray(commits) ? commits.map(String) : [],
      ended_at: said('ended_at'),
      error: said('error'),
      log: said('log'),
      ok: found.ok === true,
      reopen: said('reopen')
    }
  } catch {
    return null
  }
}
