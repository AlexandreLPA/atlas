import { describe, expect, it } from 'vitest'

import { pageLinks, twinPath, wantsMarkdown } from '../worker/routing'

describe('wantsMarkdown', () => {
  it('answers Markdown to agents that ask for it', () => {
    expect(wantsMarkdown('text/markdown')).toBe(true)
    expect(wantsMarkdown('text/markdown, text/html;q=0.9')).toBe(true)
    expect(wantsMarkdown('text/html, text/markdown')).toBe(true)
  })

  it('answers HTML to browsers and to agents that prefer it', () => {
    expect(wantsMarkdown(null)).toBe(false)
    expect(wantsMarkdown('text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')).toBe(false)
    expect(wantsMarkdown('text/html, text/markdown;q=0.5')).toBe(false)
    expect(wantsMarkdown('text/markdown;q=0')).toBe(false)
  })
})

describe('twinPath', () => {
  it('finds the Markdown twin of every page', () => {
    expect(twinPath('/')).toBe('/index.md')
    expect(twinPath('/index.html')).toBe('/index.md')
    expect(twinPath('/docs/')).toBe('/docs.md')
    expect(twinPath('/docs/install/')).toBe('/docs/install.md')
    expect(twinPath('/docs/install')).toBe('/docs/install.md')
    expect(twinPath('/news/index.html')).toBe('/news.md')
  })

  it('has no twin for files that are not pages', () => {
    expect(twinPath('/robots.txt')).toBeNull()
    expect(twinPath('/_astro/index.js')).toBeNull()
    expect(twinPath('/docs/install.md')).toBe('/docs/install.md')
  })
})

describe('pageLinks', () => {
  it('announces the twin of every page', () => {
    expect(pageLinks('/docs/install/')).toEqual(['</docs/install.md>; rel="alternate"; type="text/markdown"'])
  })

  it('announces on the home what agents read first', () => {
    const links = pageLinks('/').join(', ')
    expect(links).toContain('</llms.txt>; rel="describedby"')
    expect(links).toContain('</.well-known/agent-skills/index.json>')
    expect(links).toContain('</sitemap-index.xml>')
  })
})
