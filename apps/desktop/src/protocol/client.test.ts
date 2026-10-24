import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { EventParams } from './channel'
import { AtlasClient } from './client'
import { PROTOCOL } from './protocol.generated'

/** A WebSocket the test drives: it records what the client sends and plays the server's frames. */
class FakeSocket {
  static made: FakeSocket[] = []
  readonly sent: Record<string, unknown>[] = []
  private readonly listeners = new Map<string, Set<(event: unknown) => void>>()

  constructor(
    readonly url: string,
    readonly protocols: string[]
  ) {
    FakeSocket.made.push(this)
  }

  addEventListener(type: string, listener: (event: unknown) => void, options?: { once?: boolean }) {
    const set = this.listeners.get(type) ?? new Set()
    const wrapped = options?.once
      ? (event: unknown) => {
          set.delete(wrapped)
          listener(event)
        }
      : listener
    set.add(wrapped)
    this.listeners.set(type, set)
  }

  removeEventListener() {
    // The client removes its handshake listeners; the fake keeps calling only the live ones.
  }

  send(text: string) {
    this.sent.push(JSON.parse(text) as Record<string, unknown>)
  }

  close() {
    this.emit('close', { code: 1000 })
  }

  emit(type: string, event: unknown = {}) {
    for (const listener of [...(this.listeners.get(type) ?? [])]) {
      listener(event)
    }
  }

  frame(frame: unknown) {
    this.emit('message', { data: JSON.stringify(frame) })
  }

  ready(epoch: string) {
    this.frame({
      jsonrpc: '2.0',
      method: 'event',
      params: { payload: { epoch, version: '0.1' }, type: 'server.ready' }
    })
  }

  answer(method: string, result: unknown) {
    const call = this.sent.find(frame => frame.method === method && !('answered' in frame))

    if (!call) {
      throw new Error(`No ${method} was sent.`)
    }

    call.answered = true
    this.frame({ id: call.id, jsonrpc: '2.0', result })
  }

  delta(seq: number, text: string, conversationId = 'c1') {
    this.frame({
      method: 'event',
      params: { conversation_id: conversationId, payload: { text }, seq, type: 'message.delta' }
    })
  }
}

function start() {
  const client = new AtlasClient({
    endpoint: () => Promise.resolve({ token: 'secret', url: 'ws://127.0.0.1:1/ws' }),
    socketFactory: (url, protocols) => new FakeSocket(url, protocols) as unknown as WebSocket
  })
  const seen: EventParams[] = []
  const resynced: string[] = []
  client.onEvent(event => {
    seen.push(event)
  })
  client.onResync(id => {
    resynced.push(id)
  })
  client.start()

  return { client, resynced, seen }
}

async function opened(): Promise<FakeSocket> {
  await vi.waitFor(() => {
    expect(FakeSocket.made.at(-1)).toBeDefined()
  })
  const socket = FakeSocket.made.at(-1) as FakeSocket
  socket.emit('open')
  await Promise.resolve()

  return socket
}

const texts = (seen: EventParams[]) =>
  seen.filter(event => event.type === 'message.delta').map(event => (event.payload as { text: string }).text)

beforeEach(() => {
  FakeSocket.made = []
})

afterEach(() => {
  vi.useRealTimers()
})

describe('the connection', () => {
  it('offers the token as a subprotocol beside the protocol name, never in the URL', async () => {
    const { client } = start()
    const socket = await opened()

    expect(socket.protocols).toEqual([PROTOCOL, 'atlas.token.secret'])
    expect(socket.url).toBe('ws://127.0.0.1:1/ws')
    expect(client.connectionState).toBe('open')
    client.stop()
  })

  it('drops events at or below the number the conversation was read at', async () => {
    const { client, seen } = start()
    const socket = await opened()
    socket.ready('e1')
    client.watch('c1', 5)
    socket.delta(5, 'old')
    socket.delta(6, 'new')
    socket.delta(6, 'twice')

    expect(texts(seen)).toEqual(['new'])
    client.stop()
  })

  it('asks for what it missed after a reconnect, and paints only the gap', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { client, seen } = start()
    const first = await opened()
    first.ready('e1')
    client.watch('c1', 0)
    first.delta(1, 'one')
    first.emit('close', { code: 1006 })
    await vi.advanceTimersByTimeAsync(20_000)
    const second = await opened()
    second.ready('e1')

    expect(second.sent.find(frame => frame.method === 'conversation.events')).toMatchObject({
      params: { after: 1, conversation_id: 'c1' }
    })
    const replayed = (seq: number, text: string) => ({
      conversation_id: 'c1',
      payload: { text },
      seq,
      type: 'message.delta'
    })
    second.answer('conversation.events', {
      epoch: 'e1',
      events: [replayed(1, 'one'), replayed(2, 'two'), replayed(3, 'three')],
      open_requests: [],
      truncated: false
    })
    await vi.waitFor(() => {
      expect(texts(seen)).toEqual(['one', 'two', 'three'])
    })
    expect(seen.filter(event => event.type === 'message.delta').map(event => event.replayed)).toEqual([
      undefined,
      true,
      true
    ])
    client.stop()
  })

  it('reads the conversation again when the replay was cut, never painting the hole', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { client, resynced, seen } = start()
    const first = await opened()
    first.ready('e1')
    client.watch('c1', 4)
    first.emit('close', { code: 1006 })
    await vi.advanceTimersByTimeAsync(20_000)
    const second = await opened()
    second.ready('e1')
    second.answer('conversation.events', {
      epoch: 'e1',
      events: [{ conversation_id: 'c1', payload: { text: 'late' }, seq: 900, type: 'message.delta' }],
      open_requests: [],
      truncated: true
    })

    await vi.waitFor(() => {
      expect(resynced).toEqual(['c1'])
    })
    expect(texts(seen)).toEqual([])
    client.stop()
  })

  it('reads every conversation again when the server restarted (a new epoch)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { client, resynced } = start()
    const first = await opened()
    first.ready('e1')
    client.watch('c1', 9)
    client.watch('c2', 3)
    first.emit('close', { code: 1006 })
    await vi.advanceTimersByTimeAsync(20_000)
    const second = await opened()
    second.ready('e2')

    expect(resynced.sort()).toEqual(['c1', 'c2'])
    expect(second.sent.some(frame => frame.method === 'conversation.events')).toBe(false)
    client.stop()
  })

  it('says why a refused handshake failed', async () => {
    const { client } = start()
    const errors: string[] = []
    client.onError(error => errors.push(error.message))
    await vi.waitFor(() => {
      expect(FakeSocket.made.length).toBe(1)
    })
    FakeSocket.made[0]?.emit('close', { code: 4401 })

    await vi.waitFor(() => {
      expect(errors).toEqual(['Atlas refused the token (4401).'])
    })
    client.stop()
  })
})
