import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { BotDefinition, BotRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { BotDialogHost, slug } from './bot-dialog'
import { FACE_COLORS, faceOf } from './bot-face'
import { BotsList } from './bots-list'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$botDialog.set(null)
  app.$bots.set([])
  app.$live.set({})
  app.$marks.set({})
})

const row = (bot: Partial<BotRow> = {}): BotRow => ({
  conversation_id: null,
  description: 'Bills and taxes.',
  model: null,
  name: 'finance',
  preview: '',
  running: false,
  title: 'Finance',
  updated_at: null,
  ...bot
})

describe("a bot's face", () => {
  it('comes from its name alone: the same name, the same face', () => {
    expect(faceOf('finance', 'Finance')).toEqual(faceOf('finance', 'Finance'))
    expect(faceOf('finance', 'Finance').letter).toBe('F')
    expect(FACE_COLORS).toContain(faceOf('finance').color)
    // Renaming the title changes the letter, never the color: the name is the identity.
    expect(faceOf('finance', 'Money').color).toBe(faceOf('finance', 'Finance').color)
    expect(faceOf('finance', 'Money').letter).toBe('M')
    expect(
      new Set(['finance', 'work', 'inbox', 'notes', 'trips'].map(name => faceOf(name).color)).size
    ).toBeGreaterThan(1)
  })
})

describe('the name typed for a new bot', () => {
  it('becomes a handle a bot may have', () => {
    expect(slug('Inbox Triage')).toBe('inbox-triage')
    expect(slug('  Finanças & Impostos!  ')).toBe('financas-impostos')
    expect(slug('__notes_2')).toBe('notes_2')
    expect(slug('x'.repeat(80))).toHaveLength(64)
  })
})

describe('the bots list', () => {
  it('shows each bot with its handle and the last thing said, and opens it on a click', () => {
    const opened = vi.spyOn(app, 'openBot').mockResolvedValue()
    app.$bots.set([row({ conversation_id: 'c1', preview: 'Paid on the 5th.' }), row({ name: 'work', title: 'Work' })])

    render(<BotsList />)

    const rows = screen.getAllByTestId('bot-row')
    expect(rows.map(found => found.textContent)).toEqual([
      'FFinance@finance · Paid on the 5th.',
      'WWork@work · Bills and taxes.'
    ])
    fireEvent.click(rows[1] as HTMLElement)
    expect(opened).toHaveBeenCalledWith('work')
  })

  it("says a bot's unseen end with its title in bold, never a dot, and its turn at work in blue", () => {
    app.$bots.set([row({ conversation_id: 'c1' }), row({ conversation_id: 'c2', name: 'work', title: 'Work' })])
    app.$marks.setKey('c1', 'unread')
    app.$live.setKey('c2', true)

    render(<BotsList />)

    const [unseen, working] = screen.getAllByTestId('bot-row') as [HTMLElement, HTMLElement]
    expect(within(unseen).getByText('Finance').className).toContain('font-semibold')
    expect(within(unseen).queryByTestId('status-dot')).toBeNull()
    expect(within(working).getByText('Work').className).not.toContain('font-semibold')
    expect(within(working).getByTestId('status-dot').getAttribute('data-dot')).toBe('working')
    expect(within(working).getByTestId('status-dot').className).toContain('bg-working')
  })

  it('says what a bot is when there is none, once the list was read and never before', () => {
    app.$read.setKey('bots', false)
    const { rerender } = render(<BotsList />)
    expect(screen.queryByText(/No bots yet/)).toBeNull()
    expect(screen.getByTestId('skeleton').getAttribute('aria-label')).toBe('Reading the bots…')

    app.$read.setKey('bots', true)
    rerender(<BotsList />)
    expect(screen.getByText(/No bots yet/)).toBeTruthy()
    expect(screen.queryByTestId('skeleton')).toBeNull()
  })
})

describe('the bot dialog', () => {
  it('makes a bot from three fields, the name becoming its handle', async () => {
    const saved = vi.spyOn(app, 'saveBot').mockResolvedValue()
    app.newBot()
    render(<BotDialogHost />)

    fireEvent.change(screen.getByTestId('bot-name'), { target: { value: 'Inbox Triage' } })
    fireEvent.change(screen.getByTestId('bot-title'), { target: { value: 'Inbox Triage' } })
    fireEvent.change(screen.getByTestId('bot-description'), { target: { value: 'Sorts the inbox.' } })
    fireEvent.click(screen.getByTestId('bot-save'))

    await waitFor(() => {
      expect(saved).toHaveBeenCalledTimes(1)
    })
    const expected: BotDefinition = {
      description: 'Sorts the inbox.',
      mcp_servers: null,
      mission: '',
      model: null,
      name: 'inbox-triage',
      skills: null,
      title: 'Inbox Triage',
      tools: null
    }
    expect(saved).toHaveBeenCalledWith(expected, true)
  })

  it('says why the server refused, and stays open', async () => {
    vi.spyOn(app, 'saveBot').mockRejectedValue(new Error("There is already a bot 'finance'."))
    app.newBot()
    render(<BotDialogHost />)

    fireEvent.change(screen.getByTestId('bot-name'), { target: { value: 'finance' } })
    fireEvent.click(screen.getByTestId('bot-save'))

    expect((await screen.findByTestId('bot-error')).textContent).toBe("There is already a bot 'finance'.")
    expect(screen.getByTestId('bot-dialog')).toBeTruthy()
  })

  it('edits what a bot may use, keeps its name, and deletes only when asked twice', async () => {
    const saved = vi.spyOn(app, 'saveBot').mockResolvedValue()
    const deleted = vi.spyOn(app, 'deleteBot').mockResolvedValue()
    vi.spyOn(app, 'botOptions').mockResolvedValue({
      mcp_servers: ['bank'],
      skills: ['weekly-report'],
      tools: ['memory', 'read_file', 'run_command']
    })
    app.$botDialog.set({
      definition: {
        description: 'Bills.',
        mcp_servers: null,
        mission: 'Never pay late fees.',
        model: null,
        name: 'finance',
        skills: null,
        title: 'Finance',
        // A tool the home no longer has stays listed: saving never drops it unseen.
        tools: ['read_file', 'gone_tool']
      },
      mode: 'edit'
    })
    render(<BotDialogHost />)

    expect(screen.getByTestId('bot-name').hasAttribute('disabled')).toBe(true)
    fireEvent.click(screen.getByTestId('bot-advanced'))
    const tools = await screen.findByTestId('bot-tools')
    await waitFor(() => {
      expect(tools.textContent).toContain('run_command')
    })
    expect(tools.textContent).toContain('gone_tool')
    fireEvent.click(screen.getByTitle('memory').querySelector('input') as HTMLInputElement)
    fireEvent.click(screen.getByTestId('bot-save'))

    await waitFor(() => {
      expect(saved).toHaveBeenCalledTimes(1)
    })
    expect(saved.mock.calls[0]?.[0]).toMatchObject({
      mission: 'Never pay late fees.',
      name: 'finance',
      tools: ['read_file', 'gone_tool', 'memory']
    })
    expect(saved.mock.calls[0]?.[1]).toBe(false)

    fireEvent.click(screen.getByTestId('bot-delete'))
    expect(deleted).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('bot-delete-confirm'))
    await waitFor(() => {
      expect(deleted).toHaveBeenCalledWith('finance')
    })
  })
})
