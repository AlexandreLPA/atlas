import { createHash } from 'node:crypto'

import { describe, expect, it } from 'vitest'

import {
  AI_BOTS,
  docMarkdown,
  homeMarkdown,
  linkTwins,
  llmsFull,
  llmsTxt,
  robotsTxt,
  skillMarkdown,
  skillsIndex
} from '../src/lib/agents'
import { CONTENT_SIGNAL } from '../worker/routing'

const site = new URL('https://atlas.example.com')
const pages = [
  { id: 'docs/guides/machines', title: 'Several machines', description: 'Machines.', body: 'Body of machines.' },
  { id: 'docs', title: 'What Atlas is', description: 'Atlas.', body: 'See [Install](/docs/install/).' },
  { id: 'docs/install', title: 'Install', description: 'Install it.', body: 'Body of install.' }
]

describe('linkTwins', () => {
  it('points documentation links at their Markdown twins, keeping the anchor', () => {
    expect(linkTwins('[a](/docs/install/) [b](/docs/guides/machines/#over-ssh) [c](/docs/)', site)).toBe(
      '[a](https://atlas.example.com/docs/install.md) [b](https://atlas.example.com/docs/guides/machines.md#over-ssh) [c](https://atlas.example.com/docs.md)'
    )
  })

  it('leaves other links alone', () => {
    const text = '[x](https://example.com/docs/) [y](/news/)'
    expect(linkTwins(text, site)).toBe(text)
  })
})

describe('Markdown twins', () => {
  it('start with the title and the description', () => {
    const page = pages[1]
    expect(page).toBeDefined()
    if (page) {
      expect(docMarkdown(page, site)).toBe(
        '# What Atlas is\n\n> Atlas.\n\nSee [Install](https://atlas.example.com/docs/install.md).\n'
      )
    }
  })

  it('give the home in words an agent can act on', () => {
    const home = homeMarkdown(site)
    expect(home.startsWith('# Atlas')).toBe(true)
    expect(home).toContain('irm ')
    expect(home).toContain('curl -fsSL ')
    expect(home).toContain('https://atlas.example.com/docs.md')
  })
})

describe('llms.txt', () => {
  it('lists every documentation page in the sidebar order, each at its twin', () => {
    const text = llmsTxt(pages, site)
    const order = ['/docs.md', '/docs/install.md', '/docs/guides/machines.md'].map(path => text.indexOf(path))
    expect(order.every(at => at > 0)).toBe(true)
    expect(order).toEqual([...order].sort((a, b) => a - b))
  })

  it('puts the whole documentation in one file, in the same order', () => {
    const full = llmsFull(pages, site)
    expect(full.indexOf('# What Atlas is')).toBeLessThan(full.indexOf('# Install'))
    expect(full.indexOf('# Install')).toBeLessThan(full.indexOf('# Several machines'))
  })
})

describe('robots.txt', () => {
  it('declares the content signals, the AI bots and the sitemap', () => {
    const robots = robotsTxt(site)
    expect(robots).toContain(`Content-Signal: ${CONTENT_SIGNAL}`)
    expect(robots).toContain('Sitemap: https://atlas.example.com/sitemap-index.xml')
    for (const bot of AI_BOTS) {
      expect(robots).toContain(`User-agent: ${bot}`)
    }
  })
})

describe('the published skill', () => {
  it('has frontmatter with a valid name and a description under 1024 characters', () => {
    const skill = skillMarkdown(site)
    const front = /^---\nname: ([a-z0-9-]{1,64})\ndescription: (.+)\n---\n/.exec(skill)
    expect(front).not.toBeNull()
    expect((front?.[2] ?? '').length).toBeLessThanOrEqual(1024)
  })

  it('is listed with the digest of its exact bytes', () => {
    const index = JSON.parse(skillsIndex(site)) as { skills: { digest: string; url: string }[] }
    const digest = `sha256:${createHash('sha256').update(skillMarkdown(site), 'utf8').digest('hex')}`
    expect(index.skills[0]?.digest).toBe(digest)
    expect(index.skills[0]?.url).toBe('https://atlas.example.com/.well-known/agent-skills/atlas/SKILL.md')
  })
})
