import { describe, expect, it } from 'vitest'

import { CHAPTERS, shotAt, storyPosition } from '../src/scripts/orrery/story'

const chapters = CHAPTERS.map((_, k) => ({ top: k * 1000, height: 1000 }))

describe('storyPosition', () => {
  it('is a chapter exactly while that chapter is in the middle of the window', () => {
    CHAPTERS.forEach((_, k) => {
      expect(storyPosition(k * 1000 + 500, chapters)).toBeCloseTo(k)
    })
  })

  it('holds each chapter through its middle and moves between them', () => {
    expect(storyPosition(1500 + 150, chapters)).toBeCloseTo(1)
    const between = storyPosition(2000, chapters)
    expect(between).toBeGreaterThan(1.4)
    expect(between).toBeLessThan(1.6)
  })

  it('never goes before the first chapter or past the last', () => {
    expect(storyPosition(-5000, chapters)).toBe(0)
    expect(storyPosition(999_999, chapters)).toBe(CHAPTERS.length - 1)
  })

  it('only moves forward as the page scrolls down', () => {
    let last = -1
    for (let y = 0; y < CHAPTERS.length * 1000; y += 37) {
      const position = storyPosition(y, chapters)
      expect(position).toBeGreaterThanOrEqual(last)
      last = position
    }
  })
})

describe('shotAt', () => {
  it('opens the sphere for the accounts and closes it again at the end', () => {
    expect(shotAt(0, false).open).toBe(0)
    expect(shotAt(1, false).open).toBe(1)
    expect(shotAt(CHAPTERS.length - 1, false).open).toBe(0)
  })

  it('blends two shots halfway between their chapters', () => {
    const half = shotAt(0.5, false)
    expect(half.open).toBeCloseTo(0.5)
    expect(half.executors).toBeCloseTo(0.5)
  })

  it('puts the scene above the text on a narrow window', () => {
    for (let k = 0; k < CHAPTERS.length; k++) {
      expect(shotAt(k, true).side).toBe(0)
    }
  })
})
