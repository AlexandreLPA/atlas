/**
 * The app paints with the colors styles.css names (step 9d): a screen that wrote a palette's own
 * color, or a color for the dark theme alone, would escape the colors a user chooses later. The
 * only colors outside the named ones belong to something else than the theme: a file's kind on its
 * chip and a harness maker's mark.
 */
import { describe, expect, it } from 'vitest'

import styles from './styles.css?raw'

const SOURCES = import.meta.glob<string>(['./**/*.{ts,tsx}', '!./**/*.test.{ts,tsx}'], {
  eager: true,
  import: 'default',
  query: '?raw'
})

const HUES =
  'neutral|gray|zinc|slate|stone|red|rose|amber|yellow|orange|green|emerald|lime|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink'
const PROPERTIES =
  'bg|text|border(?:-[trblxy])?|ring|outline|fill|stroke|divide|placeholder|decoration|shadow|from|via|to|accent|caret'
const PALETTE = new RegExp(`\\b(?:${PROPERTIES})-(?:${HUES})-\\d+`, 'g')
const HEX = new RegExp(`\\b(?:${PROPERTIES})-\\[#`, 'g')
const DARK_ONLY = new RegExp(`\\bdark:(?:[\\w-]+:)*(?:${PROPERTIES})-`, 'g')

/** Their own colors, not the theme's. */
const OWN_COLORS = new Set(['./components/attachment-chip.tsx', './components/executor-avatar.tsx'])

function found(pattern: RegExp, skip: Set<string> = new Set()): string[] {
  return Object.entries(SOURCES).flatMap(([file, text]) =>
    skip.has(file) ? [] : [...text.matchAll(pattern)].map(match => `${file}: ${match[0]}`)
  )
}

/** The custom properties a block of styles.css sets, by its selector's first line. */
function names(selector: string): string[] {
  const start = styles.indexOf(`${selector} {`)
  const block = styles.slice(start, styles.indexOf('\n}', start))

  return [...block.matchAll(/^\s+(--[\w-]+):/gm)].map(match => match[1] ?? '').sort()
}

describe("the app's colors", () => {
  it('are the named ones: no palette color, written color or color for one theme alone on a screen', () => {
    expect(found(PALETTE, OWN_COLORS)).toEqual([])
    expect(found(HEX, OWN_COLORS)).toEqual([])
    expect(found(DARK_ONLY)).toEqual([])
  })

  it('are named for both themes alike', () => {
    const light = names('.light')
    expect(light).toContain('--background')
    expect(light).toContain('--working')
    expect(names('.dark')).toEqual(light)
  })

  it('each have the utility a screen paints with', () => {
    for (const name of names('.dark')) {
      expect(styles).toContain(`--color-${name.slice(2)}: var(${name});`)
    }
  })
})
