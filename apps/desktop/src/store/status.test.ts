import { describe, expect, it } from 'vitest'

import { byLastUse, conversationDot, ended, instanceDot, lastUse, worst } from './status'

describe("a row's dot", () => {
  it('shows the most urgent of what a closed row holds: a failure, then a question, then an answer', () => {
    expect(worst(['working', 'unread', '', 'waiting'])).toBe('waiting')
    expect(worst(['working', 'failed', 'waiting'])).toBe('failed')
    expect(worst(['working', 'unread'])).toBe('unread')
    expect(worst(['', ''])).toBe('')
    expect(worst([])).toBe('')
  })

  it("keeps from a turn's end an answer or a failure, and nothing from the user's own stop", () => {
    expect(ended('answered')).toBe('unread')
    expect(ended('failed')).toBe('failed')
    expect(ended('limited')).toBe('failed')
    expect(ended('halted')).toBe('failed')
    expect(ended('interrupted')).toBeNull()
  })

  it('of a conversation: a failure seen last stays until opened; a question comes before its work', () => {
    expect(conversationDot(false, false, undefined)).toBe('')
    expect(conversationDot(true, false, undefined)).toBe('working')
    expect(conversationDot(true, true, undefined)).toBe('waiting')
    expect(conversationDot(false, false, 'unread')).toBe('unread')
    expect(conversationDot(false, true, 'failed')).toBe('failed')
  })

  it('of an instance: from its state, what it asks, and what it left unseen', () => {
    expect(instanceDot('idle', false, undefined)).toBe('')
    expect(instanceDot('starting', false, undefined)).toBe('working')
    expect(instanceDot('working', true, undefined)).toBe('waiting')
    expect(instanceDot('idle', false, 'unread')).toBe('unread')
    expect(instanceDot('failed', true, 'unread')).toBe('failed')
  })
})

describe('the last used first', () => {
  it("takes a row's own time, or a later moment it was seen working, and keeps ties in order", () => {
    const rows = [
      { at: '2026-10-19T10:00:00Z', name: 'old' },
      { at: '2026-10-20T10:00:00Z', name: 'new' },
      { at: '', name: 'none' },
      { at: 'not a time', name: 'bad' }
    ]
    const seen: Record<string, number> = { old: Date.parse('2026-10-20T11:00:00Z') }

    expect(byLastUse(rows, row => lastUse(row.at, seen[row.name])).map(row => row.name)).toEqual([
      'old',
      'new',
      'none',
      'bad'
    ])
  })
})
