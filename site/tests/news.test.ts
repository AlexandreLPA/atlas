import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { draftMarkdown, fileName, PLACEHOLDER, readStory } from '../src/lib/news-draft'

const page = `<html><head>
<title>Fallback | AlphaSignal</title>
<meta property="og:title" content="Anthropic Files for IPO &amp; Shows a $16B Revenue | AlphaSignal"/>
<meta content="https://example.com/card.jpg" property="og:image"/>
<meta property="article:published_time" content="2026-10-26T13:05:46.000Z"/>
</head><body></body></html>`

describe('readStory', () => {
  it('reads the title, the picture and the day from what the page says about itself', () => {
    const story = readStory(page, 'https://example.com/news/anthropic-files-for-ipo-revealing-16b', new Date(0))
    expect(story).toEqual({
      title: 'Anthropic Files for IPO & Shows a $16B Revenue',
      url: 'https://example.com/news/anthropic-files-for-ipo-revealing-16b',
      image: 'https://example.com/card.jpg',
      date: '2026-10-26'
    })
  })

  it('falls back to the title tag and to today', () => {
    const story = readStory('<title>A plain page</title>', 'https://example.com/a', new Date('2026-01-02T00:00:00Z'))
    expect(story.title).toBe('A plain page')
    expect(story.date).toBe('2026-01-02')
    expect(story.image).toBeUndefined()
  })

  it('refuses a page with no title', () => {
    expect(() => readStory('<p>nothing</p>', 'https://example.com/a', new Date(0))).toThrow('No title')
  })
})

describe('draftMarkdown', () => {
  it('names the file by its day and the end of the address', () => {
    const story = readStory(page, 'https://example.com/news/anthropic-files-for-ipo-revealing-16b', new Date(0))
    expect(fileName(story)).toBe('2026-10-26-anthropic-files-for-ipo-revealing-16b.md')
  })

  it('leaves the summary to a person', () => {
    const story = readStory(page, 'https://example.com/news/a-story', new Date(0))
    const text = draftMarkdown(story, 'AlphaSignal')
    expect(text).toContain('title: Anthropic Files for IPO & Shows a $16B Revenue')
    expect(draftMarkdown({ ...story, title: 'A model: the details' }, 'AlphaSignal')).toContain(
      'title: "A model: the details"'
    )
    expect(text).toContain('topics: []')
    expect(text).toContain(PLACEHOLDER)
  })
})

describe('the published news', () => {
  const folder = fileURLToPath(new URL('../src/content/news/', import.meta.url))
  const items = readdirSync(folder).filter(name => name.endsWith('.md') && !name.startsWith('_'))

  it.each(items)('%s has a summary, and its links are secure', name => {
    const text = readFileSync(`${folder}${name}`, 'utf8').replace(/\r\n/g, '\n')
    const [, front = '', body = ''] = /^---\n([\s\S]*?)\n---\n([\s\S]*)$/.exec(text) ?? []
    expect(body).not.toContain(PLACEHOLDER)
    expect(body.trim().length).toBeGreaterThan(80)
    for (const key of ['url', 'image']) {
      const line = front.split('\n').find(row => row.startsWith(`${key}:`))
      if (line) {
        expect(line.slice(key.length + 1).trim()).toMatch(/^https:\/\//)
      }
    }
  })
})
