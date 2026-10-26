import { describe, expect, it } from 'vitest'

import type { ApprovalParams, ExecutorApprovalParams, ExecutorQuestionParams } from '@/protocol/protocol.generated'

import type { Card } from './app'
import type { Item, ToolStatus } from './thread'
import { waitingCalls } from './waiting'

const call = (toolId: string, name: string, status: ToolStatus = 'running'): Item => ({
  args: '{}',
  key: `k-${toolId}`,
  kind: 'tool',
  name,
  progress: [],
  result: '',
  seconds: null,
  spillPath: null,
  startedAt: null,
  status,
  toolId
})

const approval = (fields: Partial<ApprovalParams>): Card => ({
  conversationId: 'c',
  id: 'r1',
  kind: 'approval',
  params: {
    allow_always: true,
    allow_conversation: true,
    call_id: '',
    conversation_id: 'c',
    guardian_denied: false,
    reason: 'recursive delete',
    target: 'rm -r build',
    tool: 'run_command',
    ...fields
  }
})

describe('the calls a card holds', () => {
  it('is the call an approval names, not another running beside it', () => {
    const items = [call('a', 'run_command'), call('b', 'run_command')]

    expect([...waitingCalls([approval({ call_id: 'a' })], items)]).toEqual([['a', 'approval']])
  })

  it("is, from a server that names no call, the tool's last running call", () => {
    const items = [call('a', 'run_command'), call('b', 'read_file'), call('c', 'run_command')]

    expect([...waitingCalls([approval({})], items)]).toEqual([['c', 'approval']])
  })

  it('is nothing once the named call ended, and nothing without a card', () => {
    const items = [call('a', 'run_command', 'completed')]

    expect(waitingCalls([approval({ call_id: 'a' })], items).size).toBe(0)
    expect(waitingCalls([], [call('a', 'run_command')]).size).toBe(0)
  })

  it("is a question's ask_user call and a key's secrets call", () => {
    const items = [call('q', 'ask_user'), call('s', 'secrets'), call('x', 'find_files')]
    const cards: Card[] = [
      { conversationId: 'c', id: 'r1', kind: 'ask', params: { conversation_id: 'c', questions: [] } },
      {
        conversationId: 'c',
        id: 'r2',
        kind: 'secret',
        params: { bot: '', conversation_id: 'c', exists: false, name: 'KEY', reason: '', updated_at: '' }
      }
    ]

    expect(Object.fromEntries(waitingCalls(cards, items))).toEqual({ q: 'answer', s: 'key' })
  })

  it("is, in an executor's session, the call of the tool that asks, else its last running call", () => {
    const items = [call('t1', 'Read'), call('t2', 'Bash'), call('t3', 'AskUserQuestion')]
    const permission = (tool: string): Card => ({
      conversationId: '',
      id: 'r1',
      kind: 'executor-approval',
      params: { tool } as ExecutorApprovalParams
    })
    const question: Card = {
      conversationId: '',
      id: 'r2',
      kind: 'executor-question',
      params: {} as ExecutorQuestionParams
    }

    expect([...waitingCalls([permission('Bash')], items)]).toEqual([['t2', 'approval']])
    expect([...waitingCalls([permission('Write')], items)]).toEqual([['t3', 'approval']])
    expect([...waitingCalls([question], items)]).toEqual([['t3', 'answer']])
  })
})
