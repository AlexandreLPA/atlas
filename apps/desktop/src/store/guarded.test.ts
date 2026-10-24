import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { guarded } from './guarded'

describe('a change to the stores', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('leaves the other stores telling their listeners after one listener throws', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const thread = atom(0)
    const tab = atom('conversations')
    let throws = true
    const seen: string[] = []
    const stop = [
      thread.listen(() => {
        if (throws) {
          throws = false
          throw new Error('Maximum update depth exceeded.')
        }
      }),
      tab.listen(value => seen.push(value))
    ]

    guarded(() => {
      thread.set(1)
    })
    tab.set('bots')

    expect(seen).toEqual(['bots'])
    expect(console.error).toHaveBeenCalledOnce()
    for (const unbind of stop) {
      unbind()
    }
  })
})
