import { describe, expect, it } from 'vitest'

import type { SubagentRow } from '@/protocol/protocol.generated'

import { applySubagent, elapsed } from './subagents'

function row(id: string, extra: Partial<SubagentRow> = {}): SubagentRow {
  return {
    account: '',
    conversation_id: 'c-1',
    decides: 'user',
    delegation_id: 'd-1',
    ended_at: null,
    goal: `Goal ${id}`,
    last_tool: null,
    executor: null,
    folder: '',
    model: 'gpt-6-luna',
    session_id: '',
    started_at: '2026-10-12T15:00:00.000Z',
    status: 'running',
    subagent_id: id,
    tools: 0,
    waiting: 0,
    ...extra
  }
}

describe('the agent map', () => {
  it('adds a running subagent, updates it in place and lets an ended one go', () => {
    let rows = applySubagent([], row('a'))
    rows = applySubagent(rows, row('b'))
    rows = applySubagent(rows, row('a', { last_tool: 'read_file', tools: 1 }))

    expect(rows.map(kept => [kept.subagent_id, kept.last_tool])).toEqual([
      ['a', 'read_file'],
      ['b', null]
    ])

    rows = applySubagent(rows, row('a', { status: 'completed' }))
    expect(rows.map(kept => kept.subagent_id)).toEqual(['b'])
  })

  it('ignores the end of one it never saw', () => {
    expect(applySubagent([row('b')], row('x', { status: 'interrupted' }))).toEqual([row('b')])
  })

  it('tells the time a subagent has run', () => {
    const start = '2026-10-12T15:00:00.000Z'
    const at = (seconds: number) => Date.parse(start) + seconds * 1000

    expect(elapsed(start, at(45))).toBe('45s')
    expect(elapsed(start, at(185))).toBe('3m 05s')
    expect(elapsed(start, at(3720))).toBe('1h 02m')
    expect(elapsed(start, at(-5))).toBe('0s')
  })
})
