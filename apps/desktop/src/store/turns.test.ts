/**
 * The six ways a model behaves in a turn (step 9c's prototype), as the real server sent them: the
 * events and the reopened conversation in `turns/`, kept by `tests/server/test_turns.py` from the
 * fake model's scripts in `tests/turns/`. Each is replayed through the reducer: its parts, their
 * states and the interim text, and the same parts reopened as live, but for the times.
 */
import { describe, expect, it } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import type { MessageRow } from '@/protocol/protocol.generated'

import { applyEvent, EMPTY_THREAD, fromHistory, type Item, type Thread } from './thread'

interface Kept {
  events: EventParams[]
  messages: MessageRow[]
  note: string
}

const KEPT = import.meta.glob<Kept>('./turns/*.json', { eager: true, import: 'default' })

function turn(name: string): { live: Thread; reopened: Thread } {
  const kept = KEPT[`./turns/${name}.json`]

  if (!kept) {
    throw new Error(`No kept turn ${name}: run tests/server/test_turns.py with ATLAS_WRITE_TURNS=1.`)
  }

  return {
    live: kept.events.reduce((thread, event) => applyEvent(thread, event, 0), EMPTY_THREAD),
    reopened: fromHistory(kept.messages, false)
  }
}

/** What the screen shows of an item, without its key and times. */
function shown(item: Item): Record<string, unknown> {
  switch (item.kind) {
    case 'reasoning':
      return { kind: item.kind, text: item.text }
    case 'text':
      return { kind: item.kind, role: item.role, text: item.text }
    case 'tool':
      return { args: item.args, kind: item.kind, name: item.name, result: item.result, status: item.status }
    case 'user':
      return { kind: item.kind, text: item.text }
    default:
      return { kind: item.kind }
  }
}

const kinds = (thread: Thread) => thread.items.map(item => (item.kind === 'text' ? `text:${item.role}` : item.kind))
const texts = (thread: Thread, role: string) =>
  thread.items.flatMap(item => (item.kind === 'text' && item.role === role ? [item.text] : []))
const statuses = (thread: Thread) => thread.items.flatMap(item => (item.kind === 'tool' ? [item.status] : []))

describe.each(Object.keys(KEPT).map(path => path.replace('./turns/', '').replace('.json', '')))('%s', name => {
  it('reopens as it was seen live, but for the times', () => {
    const { live, reopened } = turn(name)

    expect(reopened.items.map(shown)).toEqual(live.items.map(shown))
  })

  it('has no generic part, no tag in its text, and one final answer at the end', () => {
    const { live } = turn(name)

    expect(live.items.some(item => item.kind === 'other')).toBe(false)
    expect(live.items.some(item => item.kind === 'text' && /<\/?think>/i.test(item.text))).toBe(false)
    expect(texts(live, 'final')).toHaveLength(1)
    expect(live.items.at(-1)).toMatchObject({ kind: 'text', role: 'final' })
    expect(live.running).toBe(false)
  })
})

describe('the ways of a model', () => {
  it('narrates: reasoning between the tools, an interim text before each, one call that failed', () => {
    const { live } = turn('narrates')

    expect(kinds(live)).toEqual([
      'user',
      'reasoning',
      'text:interim',
      'tool',
      'reasoning',
      'text:interim',
      'tool',
      'text:interim',
      'tool',
      'text:interim',
      'tool',
      'text:final'
    ])
    expect(statuses(live)).toEqual(['completed', 'failed', 'completed', 'completed'])
  })

  it('is silent: no text until the answer, each reasoning about its bold title', () => {
    const { live } = turn('silent')

    expect(texts(live, 'interim')).toEqual([])
    expect(kinds(live)).toEqual(['user', 'reasoning', 'tool', 'reasoning', 'tool', 'reasoning', 'tool', 'text:final'])
  })

  it('repeats: no reasoning, the diagnosis in the middle and again in the answer', () => {
    const { live } = turn('repeats')

    expect(kinds(live)).toEqual(['user', 'text:interim', 'tool', 'text:interim', 'tool', 'text:final'])
    expect(texts(live, 'interim')[1]).toContain('109.89999999999999')
    expect(texts(live, 'final')[0]).toContain('109.89999999999999')
  })

  it('puts content in the middle: the table stays on screen, interim, before a one-line answer', () => {
    const { live } = turn('content-in-the-middle')

    expect(texts(live, 'interim')[0]).toContain('| Tuesday | 12 | invalid key |')
    expect(texts(live, 'final')).toEqual(['Saved in `notes/errors.md`.'])
  })

  it('thinks aloud: a long reasoning in many pieces is one part before each tool', () => {
    const { live } = turn('thinks-aloud')

    expect(kinds(live)).toEqual([
      'user',
      'reasoning',
      'tool',
      'reasoning',
      'tool',
      'reasoning',
      'tool',
      'reasoning',
      'text:final'
    ])
    expect(live.items[1]?.kind).toBe('reasoning')
    expect(shown(live.items[1] as Item).text).toContain('read the test log first')
  })

  it('leaks <think>: the adapter took it out, and it reads as reasoning', () => {
    const { live } = turn('leaks-think')

    expect(kinds(live)).toEqual([
      'user',
      'reasoning',
      'text:interim',
      'tool',
      'reasoning',
      'text:interim',
      'tool',
      'reasoning',
      'tool',
      'reasoning',
      'text:final'
    ])
    expect(shown(live.items[1] as Item).text).toContain('I should read the log first')
  })
})
