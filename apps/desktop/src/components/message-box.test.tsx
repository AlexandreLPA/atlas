import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SlashCommandRow } from '@/protocol/protocol.generated'

import { MessageBox } from './message-box'

const COMMANDS: SlashCommandRow[] = [
  { description: 'Review a pull request', group: 'skill', hint: '[pr]', name: 'review' },
  { description: 'Clear the history but keep a summary', group: 'command', hint: '', name: 'compact' },
  { description: 'Show what fills the context', group: 'command', hint: '', name: 'context' }
]

afterEach(cleanup)

function box(onSend = vi.fn(() => Promise.resolve(true)), onSlash?: (name: string, rest: string) => boolean) {
  render(
    <MessageBox
      commands={COMMANDS}
      connected
      label="Message"
      onSend={onSend}
      onSlash={onSlash}
      placeholder="Message"
      testId="box"
      toolbar={null}
    />
  )

  return screen.getByTestId<HTMLTextAreaElement>('box')
}

const shown = () =>
  screen
    .getAllByTestId('slash-command')
    .map(row => [row.getAttribute('data-command'), row.getAttribute('aria-selected')])

describe("the box's / list", () => {
  it('shows the commands described, in their groups, and the arrows and Enter take one', () => {
    const onSend = vi.fn(() => Promise.resolve(true))
    const text = box(onSend)

    fireEvent.change(text, { target: { value: '/' } })
    const list = screen.getByTestId('slash-commands')
    expect([...list.querySelectorAll('[role=group]')].map(group => group.getAttribute('data-group'))).toEqual([
      'command',
      'skill'
    ])
    expect(list.textContent).toContain('Review a pull request')
    expect(list.textContent).toContain('[pr]')
    expect(shown()).toEqual([
      ['compact', 'true'],
      ['context', 'false'],
      ['review', 'false']
    ])

    fireEvent.keyDown(text, { key: 'ArrowDown' })
    fireEvent.keyDown(text, { key: 'ArrowDown' })
    expect(shown()[2]).toEqual(['review', 'true'])
    fireEvent.keyDown(text, { key: 'ArrowDown' })
    expect(shown()[0]).toEqual(['compact', 'true'])
    fireEvent.keyDown(text, { key: 'ArrowUp' })
    fireEvent.keyDown(text, { key: 'Enter' })

    expect(text.value).toBe('/review ')
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.queryByTestId('slash-commands')).toBeNull()
  })

  it('narrows as the name is typed, Tab takes it, and Escape hides the list until the text changes', () => {
    const text = box()

    fireEvent.change(text, { target: { value: '/con' } })
    expect(shown().map(([name]) => name)).toEqual(['context'])
    fireEvent.keyDown(text, { key: 'Escape' })
    expect(screen.queryByTestId('slash-commands')).toBeNull()
    fireEvent.change(text, { target: { value: '/co' } })
    expect(shown().map(([name]) => name)).toEqual(['compact', 'context'])
    fireEvent.keyDown(text, { key: 'Tab' })
    expect(text.value).toBe('/compact ')
  })

  it('runs on the screen a command the screen takes, and sends any other', () => {
    const onSend = vi.fn(() => Promise.resolve(true))
    const onSlash = vi.fn((name: string) => name === 'new')
    const text = box(onSend, onSlash)

    fireEvent.change(text, { target: { value: '/new' } })
    fireEvent.keyDown(text, { key: 'Escape' })
    fireEvent.keyDown(text, { key: 'Enter' })
    expect(onSlash).toHaveBeenCalledWith('new', '')
    expect(onSend).not.toHaveBeenCalled()
    expect(text.value).toBe('')

    fireEvent.change(text, { target: { value: '/review 42' } })
    fireEvent.keyDown(text, { key: 'Enter' })
    expect(onSlash).toHaveBeenLastCalledWith('review', '42')
    expect(onSend).toHaveBeenCalledWith('/review 42', [])
  })
})
