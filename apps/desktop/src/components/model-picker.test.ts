import { describe, expect, it } from 'vitest'

import type { ProviderOption } from '@/protocol/protocol.generated'
import { fromHistory } from '@/store/thread'

import { withCurrent } from './model-picker'

const OFFER: ProviderOption[] = [
  {
    id: 'openai',
    models: [
      { default_effort: 'low', efforts: ['low', 'high'], id: 'gpt-6-luna', name: 'GPT-6 Luna', reasoning: true }
    ],
    name: 'OpenAI'
  }
]

describe('the model picker', () => {
  it('keeps the offer when it holds the conversation model', () => {
    expect(withCurrent(OFFER, 'openai', 'gpt-6-luna')).toBe(OFFER)
  })

  it('adds a model the curated list lacks, under its provider or a new one', () => {
    const named = withCurrent(OFFER, 'openai', 'gpt-6-lab')
    expect(named[0]?.models.map(m => m.id)).toEqual(['gpt-6-luna', 'gpt-6-lab'])
    const local = withCurrent(OFFER, 'ollama', 'qwen')
    expect(local.map(p => p.id)).toEqual(['openai', 'ollama'])
  })

  it('leaves the model switch note out of the thread, as the user never wrote it', () => {
    const row = (number: number, kind: string, text: string) => ({
      active: true,
      attachments: [],
      call_id: null,
      calls: [],
      created_at: '',
      kind,
      number,
      reasoning: '',
      role: 'user',
      status: null,
      text,
      tool_name: null
    })
    const thread = fromHistory(
      [
        row(1, 'message', 'Hi'),
        row(2, 'model_switch', '[System: The active model for this chat has changed to x]'),
        row(3, 'message', 'Again')
      ],
      false
    )
    expect(thread.items.map(item => ('text' in item ? item.text : ''))).toEqual(['Hi', 'Again'])
  })
})
