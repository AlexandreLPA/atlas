/**
 * The tunnel to an `ssh` machine (step 15, docs/ARCHITECTURE.md, Several machines): the system's
 * `ssh` forwarding a port of this machine's loopback to the Atlas server on the other one's, so the
 * user's `~/.ssh/config`, agent and jump hosts apply.
 *
 * Its rules:
 *
 * - `BatchMode=yes`: an `ssh` the app starts never waits on a prompt nobody sees (a passphrase, a
 *   password); a key the agent does not hold fails at once, and says so.
 * - `StrictHostKeyChecking=accept-new`: the server's key is trusted the first time and checked after;
 *   a changed key fails closed, with OpenSSH's own words.
 * - `ExitOnForwardFailure=yes`, so a port that cannot be forwarded ends `ssh` instead of leaving a
 *   tunnel that carries nothing.
 * - `ServerAliveInterval`: without it a tunnel left idle stays up but dead after the laptop sleeps.
 * - **A tunnel that dies is opened again**, 5 times a minute at most, a second apart, so a blip does
 *   not tear down a healthy connection for good. The window dials again on its own.
 * - **On Windows**, the system's OpenSSH first, then Git's, then the PATH's (a fixed path breaks
 *   where OpenSSH is not installed).
 *
 * The app never starts Atlas over SSH, a way where defects pile up: the other machine's home is
 * always on, and this only reaches it.
 */
import { type ChildProcess, spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import net from 'node:net'
import path from 'node:path'

const READY_TIMEOUT_MS = 30_000
const RESTARTS_PER_MINUTE = 5
const RESTART_DELAY_MS = 1_000

/** The `ssh` to run: on Windows the system's, then Git's, then the PATH's. */
export function sshProgram(
  env: NodeJS.ProcessEnv = process.env,
  exists: (file: string) => boolean = existsSync,
  platform: NodeJS.Platform = process.platform
): string {
  if (platform !== 'win32') {
    return 'ssh'
  }

  const system = path.win32.join(env.SystemRoot ?? 'C:\\Windows', 'System32', 'OpenSSH', 'ssh.exe')
  const git = path.win32.join(env.ProgramFiles ?? 'C:\\Program Files', 'Git', 'usr', 'bin', 'ssh.exe')

  return [system, git].find(exists) ?? 'ssh'
}

/** `ssh`'s arguments for a tunnel: never a prompt, the key checked, kept alive, nothing but the
 * forwards. The host comes after `--`, so it is never read as an option. `configFile` replaces the
 * user's ~/.ssh/config (the tests' own key and known hosts). */
export function tunnelArgs(host: string, forwards: { local: number; remote: number }[], configFile?: string): string[] {
  return [
    ...(configFile ? ['-F', configFile] : []),
    '-N',
    '-o',
    'BatchMode=yes',
    '-o',
    'StrictHostKeyChecking=accept-new',
    '-o',
    'ExitOnForwardFailure=yes',
    '-o',
    'ServerAliveInterval=15',
    '-o',
    'ServerAliveCountMax=3',
    '-o',
    'ConnectTimeout=30',
    ...forwards.flatMap(({ local, remote }) => ['-L', `127.0.0.1:${local}:127.0.0.1:${remote}`]),
    '--',
    host
  ]
}

/** A port nothing listens on, on this machine's loopback. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    // Port 0 lets the system pick; the probe closes before the port is handed on.
    const probe = net.createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const bound = probe.address()
      const port = bound !== null && typeof bound === 'object' ? bound.port : null

      probe.close(() => {
        if (port === null) {
          reject(new Error('No free port.'))
        } else {
          resolve(port)
        }
      })
    })
  })
}

export interface TunnelOptions {
  host: string
  log: (line: string) => void
  /** The port of the Atlas server on the other machine. */
  remotePort: number
  /** A config file in place of the user's ~/.ssh/config: the real-sshd test's key and known hosts. */
  configFile?: string
  program?: string
  readyTimeoutMs?: number
}

/** One `ssh` forwarding a local port to the other machine's Atlas. */
export class SshTunnel {
  private child: ChildProcess | null = null
  private localPort: null | number = null
  private opening: null | Promise<number> = null
  private closed = false
  private readonly starts: number[] = []

  constructor(private readonly options: TunnelOptions) {}

  /** The local port the other machine's Atlas answers on, opening the tunnel when it is down. */
  port(): Promise<number> {
    if (this.child && this.child.exitCode === null && this.localPort !== null) {
      return Promise.resolve(this.localPort)
    }

    this.opening ??= this.open().finally(() => {
      this.opening = null
    })

    return this.opening
  }

  close(): void {
    this.closed = true
    this.child?.kill()
    this.child = null
  }

  private async open(): Promise<number> {
    if (this.closed) {
      throw new Error('The connection to this machine was closed.')
    }

    const now = Date.now()

    while (this.starts.length && now - (this.starts[0] ?? now) > 60_000) {
      this.starts.shift()
    }

    if (this.starts.length >= RESTARTS_PER_MINUTE) {
      throw new Error(`The SSH tunnel to ${this.options.host} fell ${RESTARTS_PER_MINUTE} times in a minute.`)
    }

    if (this.starts.length) {
      await new Promise(resolve => setTimeout(resolve, RESTART_DELAY_MS))
    }

    this.starts.push(Date.now())
    const local = await freePort()
    const program = this.options.program ?? sshProgram()
    const args = tunnelArgs(this.options.host, [{ local, remote: this.options.remotePort }], this.options.configFile)
    this.options.log(`Opening an SSH tunnel to ${this.options.host} (port ${this.options.remotePort} as ${local}).`)
    const child = spawn(program, args, { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true })
    this.child = child
    let said = ''
    child.stderr.on('data', (chunk: Buffer) => {
      said = (said + chunk.toString('utf8')).slice(-4_000)
    })
    const exited = new Promise<never>((_resolve, reject) => {
      child.once('error', error => {
        reject(new Error(`ssh could not be started (${program}): ${error.message}`))
      })
      child.once('exit', code => {
        if (this.child === child) {
          this.child = null
          this.localPort = null
        }

        reject(new Error(`ssh to ${this.options.host} ended (${String(code)}).${tail(said)}`))
      })
    })
    exited.catch(() => undefined)
    await Promise.race([listening(local, this.options.readyTimeoutMs ?? READY_TIMEOUT_MS), exited])
    this.localPort = local

    return local
  }
}

/** Wait until something accepts on the local port. */
async function listening(port: number, timeoutMs: number): Promise<void> {
  const giveUpAt = Date.now() + timeoutMs

  while (!(await accepts(port))) {
    if (Date.now() > giveUpAt) {
      throw new Error(`The SSH tunnel did not open in ${timeoutMs / 1000} s.`)
    }

    await new Promise(wake => setTimeout(wake, 200))
  }
}

/** One try: does a connection to the local `port` go through? */
function accepts(port: number): Promise<boolean> {
  return new Promise<boolean>(answer => {
    const probe = net.connect({ host: '127.0.0.1', port }, () => {
      probe.destroy()
      answer(true)
    })
    probe.once('error', () => {
      answer(false)
    })
  })
}

function tail(text: string): string {
  const lines = text.trim().split('\n').slice(-4).join('\n')

  return lines ? `\n${lines}` : ''
}
