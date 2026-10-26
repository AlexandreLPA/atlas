import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SecretParams } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { SecretCard } from './secret-card'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const secret = (params: Partial<SecretParams> = {}) => ({
  conversationId: 'c1',
  id: 'srq-3',
  kind: 'secret' as const,
  params: {
    bot: '',
    conversation_id: 'c1',
    exists: false,
    name: 'STRIPE_API_KEY',
    reason: 'to list my charges',
    updated_at: '',
    ...params
  }
})

describe('the secret card (step 16d)', () => {
  it('shows the name, the reason and a masked field, and sends the pasted value exactly as typed', () => {
    const answered = vi.spyOn(app, 'answerSecret').mockImplementation(() => undefined)
    render(<SecretCard card={secret()} />)

    expect(screen.getByText('to list my charges')).toBeTruthy()
    const field = screen.getByTestId<HTMLInputElement>('secret-input')
    expect(field.type).toBe('password')
    expect(field.getAttribute('autocomplete')).toBe('off')

    fireEvent.change(field, { target: { value: '  sk-live-abc  ' } })
    fireEvent.click(screen.getByTestId<HTMLButtonElement>('secret-save'))

    // Sent exactly as pasted: the spaces stay (no trim).
    expect(answered).toHaveBeenCalledWith('srq-3', { value: '  sk-live-abc  ' })
  })

  it('shows the eye, which reveals the field and hides it again', () => {
    render(<SecretCard card={secret()} />)
    const field = screen.getByTestId<HTMLInputElement>('secret-input')

    fireEvent.click(screen.getByTestId('secret-eye'))
    expect(field.type).toBe('text')
    fireEvent.click(screen.getByTestId('secret-eye'))
    expect(field.type).toBe('password')
  })

  it('warns that pasting replaces a key already stored, with the date it was written', () => {
    render(<SecretCard card={secret({ exists: true, updated_at: '2026-10-12T09:00:00Z' })} />)

    const warning = screen.getByTestId('secret-overwrite')
    expect(warning.textContent).toContain('2026-10-12T09:00:00Z')
    expect(warning.textContent).toContain('replaces')
  })

  it('does not show the overwrite warning for a key not yet stored', () => {
    render(<SecretCard card={secret()} />)
    expect(screen.queryByTestId('secret-overwrite')).toBeNull()
  })

  it('names the bot that asked, which shares the vault', () => {
    render(<SecretCard card={secret({ bot: 'finance' })} />)
    expect(screen.getByText(/finance/)).toBeTruthy()
  })

  it('skip sends skipped and no value', () => {
    const answered = vi.spyOn(app, 'answerSecret').mockImplementation(() => undefined)
    render(<SecretCard card={secret()} />)

    fireEvent.click(screen.getByTestId('secret-skip'))
    expect(answered).toHaveBeenCalledWith('srq-3', { skipped: true })
  })

  it('save is disabled while the field is blank, so an empty key is never sent', () => {
    const answered = vi.spyOn(app, 'answerSecret').mockImplementation(() => undefined)
    render(<SecretCard card={secret()} />)

    const save = screen.getByTestId<HTMLButtonElement>('secret-save')
    expect(save.disabled).toBe(true)
    fireEvent.click(save)
    expect(answered).not.toHaveBeenCalled()
  })

  it('pressing Enter in the field sends the key', () => {
    const answered = vi.spyOn(app, 'answerSecret').mockImplementation(() => undefined)
    render(<SecretCard card={secret()} />)

    const field = screen.getByTestId<HTMLInputElement>('secret-input')
    fireEvent.change(field, { target: { value: 'sk-live-xyz' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(answered).toHaveBeenCalledWith('srq-3', { value: 'sk-live-xyz' })
  })

  it('takes the keyboard as it appears when nothing has it, for the paste that follows', () => {
    render(<SecretCard card={secret()} />)

    expect(document.activeElement).toBe(screen.getByTestId('secret-input'))
  })

  it('leaves the keyboard where the user types, so their words never land in the key', () => {
    const box = document.createElement('textarea')
    document.body.append(box)
    box.focus()
    render(<SecretCard card={secret()} />)

    expect(document.activeElement).toBe(box)
    box.remove()
  })
})
