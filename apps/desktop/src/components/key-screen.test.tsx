import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { KeyProvider, ModelOptions, ProviderConnected } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { KeyScreen } from './key-screen'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$keyProviders.set(null)
  app.$keyNotice.set(null)
})

const NONE: ModelOptions = { default: { effort: null, model: null, picked: false, provider: null }, providers: [] }

const provider = (id: string, name: string, stored = false): KeyProvider => ({
  id,
  key_page: `https://${id}.example/keys`,
  name,
  stored
})

const answer = (status: ProviderConnected['status'], detail: string): ProviderConnected => ({
  detail,
  provider: 'openrouter',
  status,
  stored: status !== 'refused'
})

describe('when the key screen shows', () => {
  it('is when the picker offers nothing and chooses no model, until the user chooses later', () => {
    const keyed: ModelOptions = {
      default: { effort: null, model: 'gpt-6-luna', picked: false, provider: 'openai' },
      providers: [{ id: 'openai', models: [], name: 'OpenAI' }]
    }
    const local: ModelOptions = { ...NONE, default: { ...NONE.default, model: 'llama', provider: 'ollama' } }

    expect(app.keyScreenShown(null, false, null)).toBe(false) // not read yet: no flash
    expect(app.keyScreenShown(NONE, false, null)).toBe(true)
    expect(app.keyScreenShown(NONE, true, null)).toBe(false)
    expect(app.keyScreenShown(keyed, false, null)).toBe(false)
    expect(app.keyScreenShown(local, false, null)).toBe(false) // config.toml chose a local model
    // A key just stored with something to say stays until it is read.
    expect(app.keyScreenShown(keyed, false, answer('no_credit', 'No credit.'))).toBe(true)
  })
})

describe('the key screen', () => {
  it('connects the picked provider with the pasted key, and links to where keys are made', async () => {
    const connected = vi.spyOn(app, 'connectProvider').mockResolvedValue(answer('valid', 'The key opens OpenRouter.'))
    app.$keyProviders.set([provider('openai', 'OpenAI'), provider('openrouter', 'OpenRouter')])

    render(<KeyScreen />)
    fireEvent.click(screen.getByText('OpenRouter'))
    const field = screen.getByTestId('key-input')
    expect(field).toHaveProperty('type', 'password')
    expect(screen.getByTestId('key-connect')).toHaveProperty('disabled', true) // blank
    fireEvent.change(field, { target: { value: 'sk-or-v1-abc' } })
    fireEvent.click(screen.getByTestId('key-connect'))

    await waitFor(() => {
      expect(connected).toHaveBeenCalledWith('openrouter', 'sk-or-v1-abc')
    })
    expect(screen.getByText('Get a key').getAttribute('href')).toBe('https://openrouter.example/keys')
    await waitFor(() => {
      expect(screen.getByTestId('key-input')).toHaveProperty('value', '')
    })
  })

  it('says why a refused key was kept out, and keeps it in the field to fix', async () => {
    vi.spyOn(app, 'connectProvider').mockResolvedValue(answer('refused', 'OpenRouter refused the key (HTTP 401).'))
    app.$keyProviders.set([provider('openrouter', 'OpenRouter')])

    render(<KeyScreen />)
    fireEvent.change(screen.getByTestId('key-input'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByTestId('key-connect'))

    expect((await screen.findByTestId('key-error')).textContent).toBe('OpenRouter refused the key (HTTP 401).')
    expect(screen.getByTestId('key-input')).toHaveProperty('value', 'wrong')
  })

  it('shows what a stored key’s check said until the user continues', () => {
    const dismissed = vi.spyOn(app, 'dismissKeyNotice').mockReturnValue()
    app.$keyNotice.set(answer('no_credit', 'The key is valid; its credit or quota: none left.'))

    render(<KeyScreen />)

    expect(screen.getByTestId('key-notice').textContent).toBe('The key is valid; its credit or quota: none left.')
    fireEvent.click(screen.getByTestId('key-continue'))
    expect(dismissed).toHaveBeenCalled()
  })

  it('lets the user choose later', () => {
    const later = vi.spyOn(app, 'chooseKeyLater').mockReturnValue()
    app.$keyProviders.set([provider('openai', 'OpenAI', true)])

    render(<KeyScreen />)
    fireEvent.click(screen.getByTestId('key-later'))

    expect(later).toHaveBeenCalledWith(true)
    expect(screen.getByText('connected')).toBeTruthy()
  })
})
