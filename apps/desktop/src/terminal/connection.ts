/**
 * The side panel's terminals over the connection (step 15, docs/ARCHITECTURE.md, Several machines):
 * each shell is the server's (`terminal.open`), on the server's machine, its output coming as
 * `terminal.output` events and its end as `terminal.exit`, and each is driven here by step 16f's
 * `Terminal`, which reads its marks. One path, local too: there is no terminal in the app's main
 * process any more, so this computer's, another machine's and the container's work the same.
 *
 * - **A terminal is its connection's.** It is opened on the connection of the home on screen and
 *   stays on it while that home stays connected; when the connection drops, the server closes its
 *   shells, and their tabs close.
 * - **Output before the terminal is known is kept**: the server sends its answer first, but a
 *   piece that arrives before the answer is read is handed over, not lost.
 */
import type { AtlasClient } from '@/protocol/client'
import type { TerminalExit, TerminalOutput } from '@/protocol/protocol.generated'

import { type LateEnd, newNonce, type Pty, Terminal, type Timing, TIMING } from './terminal'

/** The exit code of a shell whose connection went: nobody can tell. */
const GONE = -1

/** A shell of the server's, as `Terminal` drives it. */
class RemotePty implements Pty {
  private readonly dataListeners: ((data: string) => void)[] = []
  private readonly exitListeners: ((event: { exitCode: number }) => void)[] = []
  private ended = false

  constructor(
    private readonly client: AtlasClient,
    private readonly id: string
  ) {}

  write(data: string): void {
    this.client.call('terminal.input', { data, id: this.id }).catch(() => undefined)
  }

  resize(cols: number, rows: number): void {
    this.client.call('terminal.resize', { cols, id: this.id, rows }).catch(() => undefined)
  }

  kill(): void {
    this.client.call('terminal.close', { id: this.id }).catch(() => undefined)
  }

  onData(listener: (data: string) => void): void {
    this.dataListeners.push(listener)
  }

  onExit(listener: (event: { exitCode: number }) => void): void {
    this.exitListeners.push(listener)
  }

  heard(data: string): void {
    for (const listener of this.dataListeners) {
      listener(data)
    }
  }

  end(exitCode: number): void {
    if (this.ended) {
      return
    }

    this.ended = true

    for (const listener of this.exitListeners) {
      listener({ exitCode })
    }
  }
}

interface Kept {
  client: AtlasClient
  pty: RemotePty
  terminal: Terminal
}

export class ConnectionTerminals implements TerminalBridge {
  private readonly kept = new Map<string, Kept>()
  /** Output of a terminal not known yet, by its id. */
  private readonly early = new Map<string, string[]>()
  /** Terminals asked for and not answered yet: only then is unknown output kept. */
  private opening = 0
  private readonly watched = new WeakSet<AtlasClient>()
  private readonly dataListeners = new Set<(id: string, text: string) => void>()
  private readonly endedListeners = new Set<(id: string, end: LateEnd) => void>()
  private readonly exitListeners = new Set<(id: string, code: number) => void>()

  /** `client`: the connection of the home on screen, which new terminals open on. */
  constructor(
    private readonly client: () => AtlasClient | null,
    private readonly timing: Timing = TIMING
  ) {}

  async available(): Promise<boolean> {
    const client = this.client()

    return client ? (await client.call('terminal.available', {})).available : false
  }

  async start(options: { cols: number; cwd: string; rows: number }): Promise<{
    id: string
    marks: boolean
    shell: string
  }> {
    const client = this.client()

    if (!client) {
      throw new Error('Atlas is not connected.')
    }

    this.watch(client)
    const nonce = newNonce()
    this.opening += 1
    const opened = await client.call('terminal.open', { ...options, nonce }).finally(() => {
      this.opening -= 1
    })
    const pty = new RemotePty(client, opened.id)
    const terminal = new Terminal(
      pty,
      opened,
      nonce,
      (id, end) => {
        for (const listener of this.endedListeners) {
          listener(id, end)
        }
      },
      this.timing
    )
    this.kept.set(opened.id, { client, pty, terminal })
    terminal.onExit(code => {
      this.kept.delete(opened.id)

      for (const listener of this.exitListeners) {
        listener(opened.id, code)
      }
    })

    for (const data of this.early.get(opened.id) ?? []) {
      pty.heard(data)
    }

    this.early.delete(opened.id)

    return { id: opened.id, marks: opened.marks, shell: opened.shell }
  }

  attach(id: string): Promise<boolean> {
    const found = this.kept.get(id)

    found?.terminal.attach(text => {
      for (const listener of this.dataListeners) {
        listener(id, text)
      }
    })

    return Promise.resolve(found !== undefined)
  }

  write(id: string, data: string): Promise<void> {
    this.kept.get(id)?.terminal.write(data)

    return Promise.resolve()
  }

  resize(id: string, cols: number, rows: number): Promise<void> {
    this.kept.get(id)?.terminal.resize(cols, rows)

    return Promise.resolve()
  }

  busy(id: string): Promise<boolean> {
    return Promise.resolve(this.kept.get(id)?.terminal.busy ?? true)
  }

  async run(id: string, command: string): Promise<TerminalRun> {
    const found = this.kept.get(id)

    if (!found) {
      throw new Error('That terminal is gone.')
    }

    return await found.terminal.run(command)
  }

  dispose(id: string): Promise<void> {
    const found = this.kept.get(id)
    this.kept.delete(id)
    found?.terminal.dispose()

    return Promise.resolve()
  }

  onData(listener: (id: string, text: string) => void): () => void {
    return this.listen(this.dataListeners, listener)
  }

  onEnded(listener: (id: string, end: LateEnd) => void): () => void {
    return this.listen(this.endedListeners, listener)
  }

  onExit(listener: (id: string, code: number) => void): () => void {
    return this.listen(this.exitListeners, listener)
  }

  private listen<T>(listeners: Set<T>, listener: T): () => void {
    listeners.add(listener)

    return () => {
      listeners.delete(listener)
    }
  }

  /** Read a connection's terminal events, once per connection. */
  private watch(client: AtlasClient): void {
    if (this.watched.has(client)) {
      return
    }

    this.watched.add(client)
    client.onEvent(event => {
      if (event.type === 'terminal.output') {
        const { data, id } = event.payload as TerminalOutput
        const found = this.kept.get(id)

        if (found?.client === client) {
          found.pty.heard(data)
        } else if (!found && this.opening > 0) {
          this.early.set(id, [...(this.early.get(id) ?? []), data])
        }
      } else if (event.type === 'terminal.exit') {
        const { code, id } = event.payload as TerminalExit
        this.early.delete(id)
        const found = this.kept.get(id)

        if (found?.client === client) {
          found.pty.end(code ?? GONE)
        }
      }
    })
    client.onState(state => {
      if (state !== 'closed' && state !== 'idle') {
        return
      }

      // The connection went, and the server closed its shells with it.
      for (const found of [...this.kept.values()]) {
        if (found.client === client) {
          found.pty.end(GONE)
        }
      }
    })
  }
}
