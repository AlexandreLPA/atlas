import { describe, expect, it } from 'vitest'

import type { ExecutorModel } from '@/protocol/protocol.generated'

import { modelOffer } from './session-view'

const model = (id: string, name: string, resolved: string, efforts: string[] = []): ExecutorModel => ({
  default_effort: efforts.includes('medium') ? 'medium' : '',
  description: '',
  efforts,
  id,
  name,
  resolved
})

/** As Claude Code 2.1.296 lists them, cut down: its default is the Opus it also lists. */
const MODELS: ExecutorModel[] = [
  model('default', 'Default (recommended)', 'claude-opus-5-5', ['low', 'medium', 'high']),
  model('opus', 'Opus 5.5', 'claude-opus-5-5', ['low', 'medium', 'high']),
  model('haiku', 'Haiku 5.5', 'claude-haiku-5-5')
]

const labels = (offer: ReturnType<typeof modelOffer>) => offer.options.map(option => option.label)

describe("an executor session's model picker", () => {
  it('shows the default when nothing is chosen, and what the last turn ran as when it differs', () => {
    expect(modelOffer(MODELS, null, '').current.id).toBe('default')
    expect(modelOffer(MODELS, null, 'claude-opus-5-5').ranAs).toBe('')
    // A model of the user's own settings runs although the pick says the default.
    expect(modelOffer(MODELS, null, 'claude-opus-5-5[1m]').ranAs).toBe('claude-opus-5-5[1m]')
  })

  it('names the default by the model it runs as, marked, and lists that model once', () => {
    expect(labels(modelOffer(MODELS, null, ''))).toEqual(['Opus 5.5 · default', 'Haiku 5.5'])
    // Chosen by its own name, it is listed beside the default.
    expect(labels(modelOffer(MODELS, 'opus', ''))).toEqual(['Opus 5.5 · default', 'Opus 5.5', 'Haiku 5.5'])
  })

  it('keeps a chosen model the list lacks, with no efforts', () => {
    const offer = modelOffer(MODELS, 'claude-opus-4-8', 'claude-opus-4-8')

    expect(offer.current).toMatchObject({ efforts: [], id: 'claude-opus-4-8' })
    expect(offer.options.map(option => option.model.id)).toEqual(['default', 'haiku', 'claude-opus-4-8'])
    expect(offer.ranAs).toBe('')
  })

  it("offers the chosen model's efforts only", () => {
    expect(modelOffer(MODELS, 'haiku', 'claude-haiku-5-5').current.efforts).toEqual([])
    expect(modelOffer(MODELS, 'default', '').current.efforts).toEqual(['low', 'medium', 'high'])
  })
})
