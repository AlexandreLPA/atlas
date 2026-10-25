import { readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { PAGES, SIDEBAR } from '../site.config.mjs'
import { sidebarPlace, sortDocs } from '../src/lib/agents'

const pages = fileURLToPath(PAGES)

/** The ids the site gives the package's pages: index.md is `docs`, guides/memory.md `docs/guides/memory`. */
function pageIds(): string[] {
  const ids: string[] = []
  for (const entry of readdirSync(pages, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      for (const name of readdirSync(`${pages}/${entry.name}`)) {
        if (name.endsWith('.md')) {
          ids.push(`docs/${entry.name}/${name.slice(0, -3)}`)
        }
      }
    } else if (entry.name.endsWith('.md')) {
      const name = entry.name.slice(0, -3)
      ids.push(name === 'index' ? 'docs' : `docs/${name}`)
    }
  }
  return ids
}

function slugs(label: string): string[] {
  const group = SIDEBAR.find(entry => entry.label === label)
  return (group?.items ?? []).flatMap(item => ('slug' in item ? [item.slug] : []))
}

describe('the sidebar', () => {
  it('shows every page of the documentation', () => {
    const hidden = pageIds().filter(id => sidebarPlace({ id })[0] === SIDEBAR.length)
    expect(hidden).toEqual([])
  })

  it('lists a folder’s pages by the order in their header, as atlas docs does', () => {
    expect(slugs('Guides').slice(0, 3)).toEqual([
      'docs/guides/coding-agents',
      'docs/guides/machines',
      'docs/guides/memory'
    ])
    expect(slugs('Reference')).toEqual(['docs/reference/commands', 'docs/reference/settings', 'docs/reference/tools'])
  })

  it('puts the pages for agents in the sidebar’s order', () => {
    const sorted = sortDocs([
      { id: 'docs/reference/tools' },
      { id: 'docs/guides/machines' },
      { id: 'docs/guides/coding-agents' },
      { id: 'docs/install' },
      { id: 'docs' }
    ])
    expect(sorted.map(page => page.id)).toEqual([
      'docs',
      'docs/install',
      'docs/guides/coding-agents',
      'docs/guides/machines',
      'docs/reference/tools'
    ])
  })
})
