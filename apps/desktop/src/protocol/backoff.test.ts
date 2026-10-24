import { describe, expect, it } from 'vitest'

import { backoffDelayMs, isStableOpen, STABLE_OPEN_MS } from './backoff'

describe('the reconnect backoff', () => {
  it('doubles from 300 ms up to 15 s', () => {
    const ladder = [0, 1, 2, 3, 4, 5, 6, 10, 1_000].map(attempt => backoffDelayMs(attempt, { jitter: false }))

    expect(ladder).toEqual([300, 600, 1_200, 2_400, 4_800, 9_600, 15_000, 15_000, 15_000])
  })

  it('waits a random time below the ceiling', () => {
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const delay = backoffDelayMs(attempt)

      expect(delay).toBeGreaterThanOrEqual(0)
      expect(delay).toBeLessThan(backoffDelayMs(attempt, { jitter: false }))
    }
  })

  it('counts a socket that died within 5 s of opening as a failure', () => {
    const now = 100_000

    expect(isStableOpen(null, now)).toBe(false)
    expect(isStableOpen(now - STABLE_OPEN_MS + 1, now)).toBe(false)
    expect(isStableOpen(now - STABLE_OPEN_MS, now)).toBe(true)
  })
})
