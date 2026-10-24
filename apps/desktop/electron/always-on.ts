/**
 * This machine's homes that are always on (step 15, docs/ARCHITECTURE.md, Several machines): the
 * app attaches to the server their service runs instead of starting its own, and turns them on,
 * off, open and closed through the same commands the terminal has (`atlas always-on`,
 * `atlas server`).
 *
 * - **A home is always on when its folder holds `always_on.json`**, which says its port. The token
 *   is the one its vault keeps, which `atlas server token` prints; it is read once per server.
 * - **A service whose server does not answer is started** (`atlas always-on start`, which waits
 *   for it), so a server that crashed or a computer that just woke is reached anyway.
 * - **The app never stops it**: it outlives the app by design.
 */
import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'

import { type Endpoint, probe, type ServerCommand } from './server'
import { withPath } from './toolchain'

const DEFAULT = 'default'
/** How long `atlas always-on on` and `start` may take: the service starts and the server migrates. */
const CLI_TIMEOUT_MS = 120_000

/** A home's folder under the homes' root: the root itself for `default`. */
export function homeFolder(root: string, name: string): string {
  return name === DEFAULT ? root : path.join(root, 'homes', name)
}

/** The home that opens when none is named (`atlas home use`), as `atlas.home.active_home` reads it. */
export function activeHome(root: string): string {
  try {
    return readFileSync(path.join(root, 'active_home'), 'utf8').trim() || DEFAULT
  } catch {
    return DEFAULT
  }
}

/** A home's always-on file, or `null` when it is not always on. */
export function readAlwaysOn(folder: string): null | { open: boolean; port: number } {
  try {
    const data = JSON.parse(readFileSync(path.join(folder, 'always_on.json'), 'utf8')) as {
      open?: unknown
      port?: unknown
    }

    return typeof data.port === 'number' ? { open: data.open === true, port: data.port } : null
  } catch {
    return null
  }
}

/** One run of the `atlas` command, for a home: its exit code and what it wrote. */
export type Cli = (home: string, args: string[]) => Promise<{ code: number; stderr: string; stdout: string }>

/** The `atlas` command the app's server runs, with other arguments: `uv run` from the repository. */
export function cliRunner(base: (home: string) => ServerCommand, env: NodeJS.ProcessEnv): Cli {
  return (home, args) =>
    new Promise((resolve, reject) => {
      const command = base(home)
      const at = command.args.indexOf('serve')
      const child = spawn(command.command, [...command.args.slice(0, at), ...args], {
        cwd: command.cwd,
        env: withPath(env, command.path),
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true
      })
      let stdout = ''
      let stderr = ''
      const timer = setTimeout(() => {
        child.kill()
        reject(new Error(`\`atlas ${args.join(' ')}\` did not finish in ${CLI_TIMEOUT_MS / 1000} s.`))
      }, CLI_TIMEOUT_MS)
      child.stdout.on('data', (chunk: Buffer) => {
        stdout += chunk.toString('utf8')
      })
      child.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString('utf8')
      })
      child.once('error', error => {
        clearTimeout(timer)
        reject(error)
      })
      child.once('exit', code => {
        clearTimeout(timer)
        resolve({ code: code ?? 1, stderr, stdout })
      })
    })
}

/** Why an `atlas` run failed, in its own words: its error line, without uv's warnings. */
export function failure(result: { stderr: string; stdout: string }): string {
  const said = result.stderr
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.startsWith('atlas:'))

  return (said.at(-1) ?? result.stderr.trim().split('\n').at(-1) ?? result.stdout.trim()).replace(/^atlas:\s*/, '')
}

/** The server of an always-on home, which the app reaches and never stops. */
export class AttachedServer {
  private token: null | string = null

  constructor(
    readonly home: string,
    private readonly folder: string,
    private readonly cli: Cli,
    private readonly log: (line: string) => void
  ) {}

  /** Its address and token, starting its service when it does not answer. */
  async endpoint(): Promise<Endpoint> {
    const state = readAlwaysOn(this.folder)

    if (!state) {
      throw new Error(`The home ${this.home} is no longer always on.`)
    }

    this.token ??= await this.readToken()
    const endpoint = { token: this.token, url: `ws://127.0.0.1:${state.port}/ws` }

    try {
      await probe(`http://127.0.0.1:${state.port}`, endpoint)
    } catch {
      this.log(`The always-on server of ${this.home} does not answer; starting its service.`)
      const started = await this.cli(this.home, ['always-on', 'start'])

      if (started.code !== 0) {
        throw new Error(failure(started))
      }

      // A token replaced meanwhile (`atlas server token --new`) is read again.
      this.token = await this.readToken()
      endpoint.token = this.token
      await probe(`http://127.0.0.1:${state.port}`, endpoint)
    }

    return endpoint
  }

  /** Read the token again: after `--new`, every connection needs the new one. */
  forget(): void {
    this.token = null
  }

  /** The app never stops an always-on server. */
  stop(): void {
    // Nothing: it is the service's.
  }

  get pid(): undefined {
    return undefined
  }

  private async readToken(): Promise<string> {
    const read = await this.cli(this.home, ['server', 'token'])

    if (read.code !== 0) {
      throw new Error(failure(read))
    }

    return read.stdout.trim().split('\n').at(-1)?.trim() ?? ''
  }
}
