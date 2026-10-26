import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ApprovalParams, AskParams, ProposalRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { ApprovalCard } from './approval-card'
import { AskCard } from './ask-card'
import { ProposalCard } from './proposal-card'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const ask = (params: Partial<AskParams> = {}) => ({
  conversationId: 'c1',
  id: 'srq-1',
  kind: 'ask' as const,
  params: { conversation_id: 'c1', questions: [], ...params }
})

const approval = (params: Partial<ApprovalParams> = {}) => ({
  conversationId: 'c1',
  id: 'srq-2',
  kind: 'approval' as const,
  params: {
    allow_always: true,
    allow_conversation: true,
    call_id: 'c1-call',
    conversation_id: 'c1',
    guardian_denied: false,
    reason: 'deletes files',
    target: 'rm -r build',
    tool: 'run_command',
    ...params
  }
})

describe("ask_user's card", () => {
  it('answers each question by its id: a choice, typed text for "Other", several where allowed', () => {
    const answered = vi.spyOn(app, 'answerAsk').mockImplementation(() => undefined)
    render(
      <AskCard
        card={ask({
          questions: [
            { choices: ['Green', 'Blue'], id: 'q0', multi_select: false, text: 'Which colour?' },
            { choices: ['A', 'B', 'C'], id: 'q1', multi_select: true, text: 'Which letters?' },
            { choices: [], id: 'q2', multi_select: false, text: 'Your name?' },
            { choices: ['Yes', 'No'], id: 'q3', multi_select: false, text: 'Sure?' }
          ]
        })}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Blue' }))
    fireEvent.click(screen.getByRole('button', { name: 'A' }))
    fireEvent.click(screen.getByRole('button', { name: 'C' }))
    fireEvent.change(screen.getByLabelText('Your name? Your answer'), { target: { value: 'Ana' } })
    const others = screen.getAllByRole('button', { name: 'Other' })
    fireEvent.click(others[2] as HTMLElement)
    fireEvent.change(screen.getByLabelText('Sure? Your answer'), { target: { value: 'Maybe' } })
    fireEvent.click(screen.getByRole('button', { name: 'Answer' }))

    expect(answered).toHaveBeenCalledWith('srq-1', { answers: { q0: 'Blue', q1: ['A', 'C'], q2: 'Ana', q3: 'Maybe' } })
  })

  it('skips with no answers, which the server reads as dismissed', () => {
    const answered = vi.spyOn(app, 'answerAsk').mockImplementation(() => undefined)
    render(<AskCard card={ask({ questions: [{ choices: ['x'], id: 'q0', multi_select: false, text: 'Q?' }] })} />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))

    expect(answered).toHaveBeenCalledWith('srq-1', {})
  })
})

describe('the approval card', () => {
  it('offers once, this conversation, always and deny, and passes a reason with deny', () => {
    const answered = vi.spyOn(app, 'answerApproval').mockImplementation(() => undefined)
    render(<ApprovalCard card={approval()} />)

    expect(screen.getAllByRole('button').map(button => button.textContent)).toEqual([
      'Allow once',
      'This conversation',
      'Always',
      'Deny'
    ])
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'use git clean' } })
    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))

    expect(answered).toHaveBeenCalledWith('srq-2', { choice: 'deny', reason: 'use git clean' })
  })

  it('offers only once and deny when the guardian judged it dangerous', () => {
    render(<ApprovalCard card={approval({ guardian_denied: true })} />)

    expect(screen.getAllByRole('button').map(button => button.textContent)).toEqual(['Allow once', 'Deny'])
  })
})

const proposal = (row: Partial<ProposalRow> = {}): ProposalRow => ({
  bot: null,
  changes: [
    { action: 'replace', content: 'Lives in Lisbon; works remotely.', entry: 'Lives in Porto; works remotely.' },
    { action: 'remove', content: '', entry: 'Old habit.' },
    { action: 'add', content: 'Likes tea.', entry: null }
  ],
  conversation_id: 'c1',
  created_at: '2026-10-11T05:40:12.345Z',
  id: '3f9a1c2e',
  problem: null,
  target: 'user',
  ...row
})

describe('a memory proposal', () => {
  it('marks the words a replace changes, keeps both texts whole a click away, to approve or discard', () => {
    const approved = vi.spyOn(app, 'approveProposal').mockResolvedValue(undefined)
    render(<ProposalCard proposal={proposal()} />)

    expect(screen.getByRole('region', { name: 'Change to your profile' })).toBeTruthy()
    const diff = screen.getByTestId('proposal-diff')
    expect([...diff.querySelectorAll('del')].map(node => node.textContent)).toEqual(['Porto'])
    expect([...diff.querySelectorAll('ins')].map(node => node.textContent)).toEqual(['Lisbon'])
    expect(screen.getByText('Lives in Porto; works remotely.')).toBeTruthy()
    expect(screen.getByText('Lives in Lisbon; works remotely.')).toBeTruthy()
    expect(screen.getByText('Old habit.')).toBeTruthy()
    expect(screen.getByText('Likes tea.')).toBeTruthy()
    expect(screen.getAllByRole('button').map(button => button.textContent)).toEqual(['Approve', 'Discard'])
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    expect(approved).toHaveBeenCalledWith(proposal())
  })

  it('says why one can no longer apply, and offers only to discard it', () => {
    const discarded = vi.spyOn(app, 'discardProposal').mockResolvedValue(undefined)
    render(<ProposalCard proposal={proposal({ problem: 'An entry changed since this was proposed.' })} />)

    expect(screen.getByTestId('proposal-problem').textContent).toBe('An entry changed since this was proposed.')
    expect(screen.getAllByRole('button').map(button => button.textContent)).toEqual(['Discard'])
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))

    expect(discarded).toHaveBeenCalledWith('3f9a1c2e')
  })

  it('says what an approval overwrote and removed, until it is put away', () => {
    const putAway = vi.spyOn(app, 'putAwayApproved').mockImplementation(() => undefined)
    render(
      <ProposalCard
        approved={{
          applied: true,
          error: null,
          removed: ['Old habit.'],
          replaced: ['Lives in Porto; works remotely.']
        }}
        proposal={proposal()}
      />
    )

    const applied = screen.getByTestId('proposal-applied')
    expect(applied.textContent).toContain('Overwrote, whole:Lives in Porto; works remotely.')
    expect(applied.textContent).toContain('Removed:Old habit.')
    expect(applied.textContent).toContain('Ask Atlas to add back anything you still want.')
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))

    expect(putAway).toHaveBeenCalledWith('3f9a1c2e')
  })
})
