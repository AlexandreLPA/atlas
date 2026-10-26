import { existsSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { PAGES } from '../site.config.mjs'
import { catalogue, faq, seo } from '../src/lib/home'
import { application, docPage, faqPage, jsonLd } from '../src/lib/seo'
import { searchTitle } from '../src/lib/seo-titles'

const site = new URL('https://atlas.example.com')

/** Whether the documentation has the page a site path names: /docs/guides/memory/ is guides/memory.md. */
function hasPage(path: string): boolean {
  const name = path.replace(/^\/docs\/?/, '').replace(/\/$/, '') || 'index'
  return existsSync(new URL(`${name}.md`, PAGES))
}

describe('the home in a search result', () => {
  it('fits the title and the description where results cut them', () => {
    expect(seo.title.length).toBeLessThanOrEqual(70)
    expect(seo.description.length).toBeLessThanOrEqual(170)
  })

  it('links every card and every answer to a page the documentation has', () => {
    for (const href of [...catalogue.items.map(item => item.href), ...faq.items.flatMap(item => item.href ?? [])]) {
      expect(hasPage(href.split('#')[0] ?? href), href).toBe(true)
    }
  })
})

describe('searchTitle', () => {
  it('gives a known page its search title, naming Atlas once', () => {
    expect(searchTitle('docs/guides/models', 'Models and keys')).toBe(
      'Models and API keys: OpenAI, Anthropic, Gemini, Ollama, vLLM | Atlas'
    )
    expect(searchTitle('docs/faq', 'Questions and answers')).toBe(
      'Atlas FAQ: accounts, usage limits, privacy and models'
    )
  })

  it('falls back to the page’s own title', () => {
    expect(searchTitle('docs/guides/something-new', 'Something new')).toBe('Something new | Atlas docs')
  })

  it('keeps every special title short enough to show whole', () => {
    for (const id of ['docs', 'docs/install', 'docs/guides/coding-agents', 'docs/guides/models', 'docs/faq']) {
      expect(searchTitle(id, '').length, id).toBeLessThanOrEqual(70)
    }
  })
})

describe('structured data', () => {
  it('describes the app as free software for the three systems', () => {
    const app = application(site)
    expect(app['@type']).toBe('SoftwareApplication')
    expect(app['operatingSystem']).toBe('Windows, macOS, Linux')
    expect(app['offers']).toEqual({ '@type': 'Offer', price: '0', priceCurrency: 'USD' })
  })

  it('answers each question of the home in its FAQ', () => {
    const entities = faqPage(site)['mainEntity'] as { name: string }[]
    expect(entities.map(entity => entity.name)).toEqual(faq.items.map(item => item.question))
  })

  it('leads a page’s trail from the home through the docs, each step a page', () => {
    const [, trail] = docPage(site, { path: '/docs/install/', title: 'Install', description: 'Install it.' })
    expect(trail?.['itemListElement']).toEqual([
      { '@type': 'ListItem', position: 1, name: 'Atlas', item: 'https://atlas.example.com/' },
      { '@type': 'ListItem', position: 2, name: 'Docs', item: 'https://atlas.example.com/docs/' },
      { '@type': 'ListItem', position: 3, name: 'Install', item: 'https://atlas.example.com/docs/install/' }
    ])
  })

  it('never lets a string close the script element it sits in', () => {
    const text = jsonLd([{ '@type': 'Thing', name: '</script><script>alert(1)</script>' }])
    expect(text).not.toContain('</script>')
    const parsed = JSON.parse(text) as { '@graph': { name: string }[] }
    expect(parsed['@graph'][0]?.name).toBe('</script><script>alert(1)</script>')
  })
})
