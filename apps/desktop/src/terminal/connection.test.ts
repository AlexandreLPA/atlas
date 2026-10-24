/**
 * The terminals over the connection (step 15): what goes to the server and what comes back, a
 * connection standing in for the real one. The real server's side is `tests/server/test_terminal.py`,
 * and the whole path the e2e's terminal tests, through the real app.
 */
import { describe, expect, it } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import type { AtlasClient, ConnectionState } from '@/protocol/client'

import { ConnectionTerminals } from './connection'
import { TIMING } from './terminal'

/** A connection that answers `terminal.*` and lets the test send events and drop it. */
class FakeConnection {
  calls: [string, Record<string, unknown>][] = []
  private events: ((event: EventParams) => void)[] = []
  private states: ((state: ConnectionState) => void)[] = []
  /** Events sent before `terminal.open` answers, as a racing server would. */
  before: EventParams[] = []

  call(method: string, params: Record<string, unknown>): Promise<unknown> {
    this.calls.push([method, params])

    if (method === 'terminal.available') {
      return Promise.resolve({ available: true, reason: '' })
    }

    if (method === 'terminal.open') {
      for (const event of this.before) {
        this.emit(event)
      }

      return Promise.resolve({ id: 'shell-1', marks: true, shell: 'bash' })
    }

    return Promise.resolve({})
  }

  onEvent(listener: (event: EventParams) => void): () => void {
    this.events.push(listener)

    return () => undefined
  }

  onState(listener: (state: ConnectionState) => void): () => void {
    this.states.push(listener)

    return () => undefined
  }

  emit(event: EventParams): void {
    for (const listener of this.events) {
      listener(event)
    }
  }

  drop(): void {
    for (const listener of this.states) {
      listener('closed')
    }
  }

  get client(): AtlasClient {
    return this as unknown as AtlasClient
  }
}

const output = (id: string, data: string): EventParams => ({ payload: { data, id }, type: 'terminal.output' })

function terminals(connection: FakeConnection | null): ConnectionTerminals {
  return new ConnectionTerminals(() => connection?.client ?? null, { ...TIMING, readyMs: 500 })
}

describe('the terminals over the connection', () => {
  it('asks the server on screen whether it opens terminals, and opens one there with a nonce', async () => {
    const connection = new FakeConnection()
    const host = terminals(connection)

    expect(await host.available()).toBe(true)
    expect(await host.start({ cols: 80, cwd: '/srv/work', rows: 24 })).toEqual({
      id: 'shell-1',
      marks: true,
      shell: 'bash'
    })
    const [method, params] = connection.calls[1] ?? []
    expect(method).toBe('terminal.open')
    expect(params).toMatchObject({ cols: 80, cwd: '/srv/work', rows: 24 })
    expect(params?.nonce).toMatch(/^[0-9a-f]{32}$/)

    expect(await terminals(null).available()).toBe(false)
    await expect(terminals(null).start({ cols: 80, cwd: '', rows: 24 })).rejects.toThrow('not connected')
  })

  it('types, resizes and closes through the server, and shows what came, even before the answer', async () => {
    const connection = new FakeConnection()
    connection.before = [output('shell-1', 'early ')]
    const host = terminals(connection)
    const shown: string[] = []
    host.onData((id, text) => shown.push(`${id}:${text}`))
    await host.start({ cols: 80, cwd: '', rows: 24 })

    connection.emit(output('shell-1', 'later'))
    connection.emit(output('another', 'not ours'))
    await host.attach('shell-1')
    await host.write('shell-1', 'ls\r')
    await host.resize('shell-1', 100, 30)
    await host.dispose('shell-1')

    expect(shown).toEqual(['shell-1:early later'])
    expect(connection.calls.map(([method]) => method)).toEqual([
      'terminal.open',
      'terminal.input',
      'terminal.resize',
      'terminal.close'
    ])
    expect(connection.calls[1]?.[1]).toEqual({ data: 'ls\r', id: 'shell-1' })
    expect(await host.busy('shell-1')).toBe(true)
    await expect(host.run('shell-1', 'ls')).rejects.toThrow('gone')
  })

  it('closes its tabs when the shell exits, or when the connection goes', async () => {
    const connection = new FakeConnection()
    const host = terminals(connection)
    const exits: string[] = []
    host.onExit((id, code) => exits.push(`${id}:${String(code)}`))

    await host.start({ cols: 80, cwd: '', rows: 24 })
    connection.emit({ payload: { code: 0, id: 'shell-1' }, type: 'terminal.exit' })
    await host.start({ cols: 80, cwd: '', rows: 24 })
    connection.drop()

    expect(exits).toEqual(['shell-1:0', 'shell-1:-1'])
  })
})
