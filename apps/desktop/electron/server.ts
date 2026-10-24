/**
 * The Atlas server this app talks to: one it starts and owns, or one already running elsewhere.
 *
 * Decided on 11/10/2026 (docs/ARCHITECTURE.md, Desktop app):
 *
 * - **The app starts `atlas serve --host 127.0.0.1 --port 0 --parent-pid <its pid>`** with a fresh
 *   token in `ATLAS_SERVER_TOKEN`, reads the port from the ready line on standard output, and calls
 *   the server ready only after `/health` and a WebSocket with the token both answered: an HTTP probe
 *   that passes while the socket refuses is "it said connected but nothing works".
 * - **The ready line is watched from the first byte**: a line printed before the watcher attached
 *   would be lost, and a healthy server timed out.
 * - **A cold start gets 90 seconds**: the first run compiles and imports everything, and on Windows
 *   the antivirus scans each new file (30 to 60 s seen).
 * - **The server leaves with the app**: `--parent-pid` makes it stop within seconds of this process
 *   ending, however it ended. On quit the app stops it at once: the whole process group with
 *   SIGTERM on POSIX, and the whole tree with `taskkill /T /F` on Windows, which has no such signal
 *   for a console process. Waiting for the watchdog there is a deadlock: Electron does not exit
 *   while the server's pipes are open, and the server waits for Electron to exit (seen on
 *   11/10/2026, one quit in three).
 * - **A server that dies unasked is started again**, at most 3 times a minute, and the window's
 *   next dial finds the new port and token.
 * - **`ATLAS_DESKTOP_URL` and `ATLAS_DESKTOP_TOKEN`** name a server already running; then nothing
 *   is started.
 */
import { type ChildProcess, execFileSync, spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import path from 'node:path'

import { PROTOCOL, READY_LINE, TOKEN_PREFIX } from '../src/protocol/protocol.generated'

import { toolchainFolders, withPath } from './toolchain'

export interface Endpoint {
  token: string
  url: string
}

export interface ServerCommand {
  args: string[]
  command: string
  cwd: string
  /** Folders put first on the server's PATH: the installation's toolchain, or none. */
  path: string[]
}

export const READY_TIMEOUT_MS = 90_000
const PROBE_TIMEOUT_MS = 10_000
const RESTARTS_PER_MINUTE = 3

/** The checkout the app sits in (`apps/desktop` under it). */
export function repositoryRoot(appRoot: string): string {
  return path.resolve(appRoot, '..', '..')
}

/**
 * `atlas serve` from the repository the app sits in, through uv (the packaged app comes in step
 * 18b), for a named home, or for the one `atlas home use` chose when `home` is `null`. The uv is
 * the installation's own when its toolchain names one (`programs`, from `readToolchain`), and the
 * toolchain's folders lead the server's PATH; a development checkout has none, and runs the PATH's.
 */
export function repositoryCommand(
  appRoot: string,
  parentPid: number,
  home: null | string = null,
  programs: Record<string, string> = {}
): ServerCommand {
  const root = repositoryRoot(appRoot)

  return {
    args: [
      'run',
      '--project',
      root,
      'atlas',
      ...(home === null ? [] : ['--home', home]),
      'serve',
      '--host',
      '127.0.0.1',
      '--port',
      '0',
      '--parent-pid',
      String(parentPid)
    ],
    command: programs.uv ?? 'uv',
    cwd: root,
    path: toolchainFolders(programs)
  }
}

/** The port in the ready line, once the output so far holds it. */
export function readyPort(output: string): null | number {
  const at = output.indexOf(READY_LINE)

  if (at < 0) {
    return null
  }

  const match = /^(\d+)\r?\n/.exec(output.slice(at + READY_LINE.length))

  return match?.[1] ? Number(match[1]) : null
}

/** The remote server the environment names, or `null`. Half a pair is a mistake worth saying. */
export function remoteEndpoint(env: NodeJS.ProcessEnv): Endpoint | null {
  const url = env.ATLAS_DESKTOP_URL?.trim()
  const token = env.ATLAS_DESKTOP_TOKEN?.trim()

  if (!url && !token) {
    return null
  }

  if (!url || !token) {
    throw new Error('Set both ATLAS_DESKTOP_URL and ATLAS_DESKTOP_TOKEN, or neither.')
  }

  const parsed = new URL(url)

  if (parsed.protocol !== 'ws:' && parsed.protocol !== 'wss:') {
    throw new Error(`ATLAS_DESKTOP_URL must be a ws:// or wss:// address, not ${parsed.protocol}`)
  }

  return { token, url }
}

export interface ServerOptions {
  command: ServerCommand
  /** The environment the server starts with; the token is added to it. */
  env: NodeJS.ProcessEnv
  log: (line: string) => void
  readyTimeoutMs?: number
}

/** A server this app started, and starts again when it dies unasked. */
export class OwnedServer {
  private child: ChildProcess | null = null
  private current: Endpoint | null = null
  private starting: null | Promise<Endpoint> = null
  private stopping = false
  private readonly restarts: number[] = []
  private readonly options: ServerOptions

  constructor(options: ServerOptions) {
    this.options = options
  }

  /** The server's address and token, starting it when it is not running. */
  endpoint(): Promise<Endpoint> {
    if (this.current) {
      return Promise.resolve(this.current)
    }

    this.starting ??= this.start().finally(() => {
      this.starting = null
    })

    return this.starting
  }

  get pid(): number | undefined {
    return this.child?.pid
  }

  /** Stop the server and do not start it again. */
  stop(): void {
    this.stopping = true
    const child = this.child

    if (!child?.pid || child.exitCode !== null) {
      return
    }

    try {
      if (process.platform === 'win32') {
        execFileSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore', windowsHide: true })
      } else {
        // The whole group: uv and the server under it stop gracefully.
        process.kill(-child.pid, 'SIGTERM')
      }
    } catch {
      // Already gone.
    }
  }

  private async start(): Promise<Endpoint> {
    if (this.stopping) {
      throw new Error('The app is closing.')
    }

    const token = randomBytes(32).toString('base64url')
    const { args, command, cwd, path: folders } = this.options.command
    this.options.log(`Starting Atlas: ${command} ${args.join(' ')}`)
    const child = spawn(command, args, {
      // Its own process group on POSIX, so a stop reaches uv and the server under it.
      detached: process.platform !== 'win32',
      cwd,
      env: { ...withPath(this.options.env, folders), ATLAS_SERVER_TOKEN: token },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
    this.child = child

    let output = ''
    let errors = ''
    const port = new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(
          new Error(
            `Atlas did not say it was ready in ${(this.options.readyTimeoutMs ?? READY_TIMEOUT_MS) / 1000} s.${tail(errors)}`
          )
        )
      }, this.options.readyTimeoutMs ?? READY_TIMEOUT_MS)

      child.stdout.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
        const found = readyPort(output)

        if (found !== null) {
          clearTimeout(timer)
          resolve(found)
        }
      })
      child.stderr.on('data', (chunk: Buffer) => {
        const text = chunk.toString('utf8')
        errors = (errors + text).slice(-8_000)
        this.options.log(text.trimEnd())
      })
      child.once('error', error => {
        clearTimeout(timer)
        reject(new Error(`Atlas could not be started (${command}): ${error.message}`))
      })
      child.once('exit', (code, signal) => {
        clearTimeout(timer)
        reject(new Error(`Atlas stopped before it was ready (${signal ?? String(code)}).${tail(errors)}`))
      })
    })

    child.once('exit', (code, signal) => {
      this.options.log(`Atlas stopped (${signal ?? String(code)}).`)

      if (this.child === child) {
        this.child = null
        this.current = null
      }

      if (!this.stopping && this.mayRestart()) {
        void this.endpoint().catch((error: unknown) => {
          this.options.log(`Atlas could not be started again: ${String(error)}`)
        })
      }
    })

    const found = await port
    const endpoint = { token, url: `ws://127.0.0.1:${found}/ws` }
    await probe(`http://127.0.0.1:${found}`, endpoint)
    this.current = endpoint
    this.options.log(`Atlas is ready on port ${found}.`)

    return endpoint
  }

  private mayRestart(): boolean {
    const now = Date.now()

    while (this.restarts.length && now - (this.restarts[0] ?? now) > 60_000) {
      this.restarts.shift()
    }

    if (this.restarts.length >= RESTARTS_PER_MINUTE) {
      return false
    }

    this.restarts.push(now)

    return true
  }
}

/** `/health` answers, and a WebSocket with the token opens and gets `server.ready`. */
export async function probe(base: string, endpoint: Endpoint, timeoutMs = PROBE_TIMEOUT_MS): Promise<void> {
  const health = await fetch(`${base}/health`, { signal: AbortSignal.timeout(timeoutMs) })

  if (!health.ok) {
    throw new Error(`Atlas's /health answered ${health.status}.`)
  }

  await new Promise<void>((resolve, reject) => {
    const socket = new WebSocket(endpoint.url, [PROTOCOL, TOKEN_PREFIX + endpoint.token])
    const timer = setTimeout(() => {
      socket.close()
      reject(new Error('Atlas answered /health but its WebSocket did not.'))
    }, timeoutMs)

    socket.addEventListener('message', () => {
      clearTimeout(timer)
      socket.close()
      resolve()
    })
    socket.addEventListener('close', event => {
      clearTimeout(timer)
      reject(new Error(`Atlas answered /health but refused the WebSocket (${event.code}).`))
    })
  })
}

function tail(text: string): string {
  const lines = text.trim().split('\n').slice(-5).join('\n')

  return lines ? `\n${lines}` : ''
}
