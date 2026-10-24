import { describe, expect, it } from 'vitest'

import type { CommandRow } from '@/protocol/protocol.generated'

import { applyCommand, commandName } from './commands'

function row(id: string, extra: Partial<CommandRow> = {}): CommandRow {
  return {
    command: `build ${id}`,
    command_id: id,
    description: '',
    ended_at: null,
    exit_code: null,
    started_at: '2026-10-14T15:00:00.000Z',
    status: 'running',
    ...extra
  }
}

describe('the background tasks of the agent map', () => {
  it('adds a command, and replaces it in place when it ends, keeping it to show how', () => {
    let rows = applyCommand([], row('cmd-1'))
    rows = applyCommand(rows, row('cmd-2'))
    rows = applyCommand(rows, row('cmd-1', { exit_code: 0, status: 'completed' }))

    expect(rows.map(kept => [kept.command_id, kept.status])).toEqual([
      ['cmd-1', 'completed'],
      ['cmd-2', 'running']
    ])
  })

  it('keeps the newest twenty ended commands and every running one', () => {
    let rows = applyCommand([], row('cmd-0'))

    for (let index = 1; index <= 25; index += 1) {
      rows = applyCommand(rows, row(`cmd-${String(index)}`, { status: 'failed' }))
    }

    expect(rows).toHaveLength(21)
    expect(rows[0]?.command_id).toBe('cmd-0')
    expect(rows[1]?.command_id).toBe('cmd-6')
  })

  it('names a command by its description, or by its command when the model gave none', () => {
    expect(commandName(row('cmd-1', { description: 'Build the game' }))).toBe('Build the game')
    expect(commandName(row('cmd-2', { command: '  npm run build\nnpm test' }))).toBe('npm run build')
    expect(commandName(row('cmd-3', { command: '' }))).toBe('cmd-3')
  })
})
