import { describe, expect, it } from 'vitest'

import {
  executorPosition,
  executors,
  moonPosition,
  moons,
  orbitRadius,
  rankOf,
  ROUTE_PERIOD,
  routeMoment,
  strandOfRank,
  STRANDS
} from '../src/scripts/orrery/model'

describe('orbits', () => {
  it('gives every strand its own orbit, from the outside in', () => {
    const ranks = Array.from({ length: STRANDS }, (_, strand) => rankOf(strand))
    expect([...ranks].sort((a, b) => a - b)).toEqual(Array.from({ length: STRANDS }, (_, k) => k))
    ranks.forEach((rank, strand) => {
      expect(strandOfRank(rank)).toBe(strand)
    })
  })

  it('makes the equator the outermost orbit', () => {
    expect(rankOf((STRANDS - 1) / 2)).toBe(0)
    for (let rank = 1; rank < STRANDS; rank++) {
      expect(orbitRadius(rank)).toBeLessThan(orbitRadius(rank - 1))
    }
  })

  it('keeps each coding agent on its own orbit, at that radius', () => {
    expect(new Set(executors.map(e => e.rank)).size).toBe(executors.length)
    for (const executor of executors) {
      const [x, y, z] = executorPosition(executor, 12.5)
      expect(Math.hypot(x, z)).toBeCloseTo(orbitRadius(executor.rank))
      expect(y).toBe(0)
    }
  })

  it('keeps the accounts close to their harness', () => {
    for (const moon of moons) {
      const host = executors.find(e => e.key === moon.host)
      expect(host).toBeDefined()
      if (host) {
        const [hx, , hz] = executorPosition(host, 3)
        const [mx, , mz] = moonPosition(moon, 3)
        expect(Math.hypot(mx - hx, mz - hz)).toBeLessThanOrEqual(moon.radius + 1e-9)
      }
    }
  })
})

describe('routeMoment', () => {
  it('tells the route in order: personal runs out, the session hops to company, then to Codex', () => {
    expect(routeMoment(0.5).states).toEqual(['running', 'idle', 'idle'])
    expect(routeMoment(2).hop).toBe(0)
    expect(routeMoment(4).states).toEqual(['limit', 'running', 'idle'])
    expect(routeMoment(5.5).hop).toBe(1)
    expect(routeMoment(8).states).toEqual(['limit', 'limit', 'running'])
  })

  it('loops, with every meter between empty and full', () => {
    for (let t = 0; t < ROUTE_PERIOD * 2; t += 0.1) {
      const moment = routeMoment(t)
      const later = routeMoment(t + ROUTE_PERIOD)
      expect(later.states).toEqual(moment.states)
      expect(later.hop).toBe(moment.hop)
      moment.meters.forEach((meter, k) => {
        expect(meter).toBeGreaterThanOrEqual(0)
        expect(meter).toBeLessThanOrEqual(1)
        expect(later.meters[k]).toBeCloseTo(meter, 6)
      })
      expect(moment.along).toBeGreaterThanOrEqual(0)
      expect(moment.along).toBeLessThanOrEqual(1)
    }
  })
})
