import { describe, expect, it } from 'vitest'

import type { SlashCommandRow } from '@/protocol/protocol.generated'

import { fromNames, offered, PER_GROUP, slashQuery } from './slash'

const row = (name: string, group = 'skill', description = ''): SlashCommandRow => ({
  description,
  group,
  hint: '',
  name
})

const ROWS = [
  row('review', 'skill', 'Review a pull request'),
  row('compact', 'command', 'Clear the conversation history but keep a summary'),
  row('marketing:seo-audit', 'skill', 'Run an SEO audit'),
  row('context', 'command', 'Show what fills the context'),
  row('code-review', 'skill', 'Review the diff for bugs')
]

const names = (query: string) => offered(ROWS, query).map(group => [group.group, group.rows.map(found => found.name)])

describe('what the box offers as / is typed', () => {
  it('shows only while a name is typed, nothing after it', () => {
    expect(slashQuery('/')).toBe('')
    expect(slashQuery('/rev')).toBe('rev')
    expect(slashQuery('/review the diff')).toBeNull()
    expect(slashQuery('hi /rev')).toBeNull()
  })

  it('lists every one for a bare slash, the commands first, each group as its source listed it', () => {
    expect(names('')).toEqual([
      ['command', ['compact', 'context']],
      ['skill', ['review', 'marketing:seo-audit', 'code-review']]
    ])
  })

  it('matches by tiers: the name, its start, inside it, then a word of its description', () => {
    expect(names('review')).toEqual([['skill', ['review', 'code-review']]])
    expect(names('co')).toEqual([
      ['command', ['compact', 'context']],
      ['skill', ['code-review']]
    ])
    expect(names('seo')).toEqual([['skill', ['marketing:seo-audit']]])
    expect(names('bugs')).toEqual([['skill', ['code-review']]])
    expect(names('REV')).toEqual([['skill', ['review', 'code-review']]])
    expect(names('zzz')).toEqual([])
  })

  it('keeps at most 30 of each group', () => {
    const many = Array.from({ length: 40 }, (_, index) => row(`skill-${String(index)}`))

    expect(offered(many, 'skill')[0]?.rows).toHaveLength(PER_GROUP)
  })

  it('offers bare names while the described list is read', () => {
    expect(fromNames(['compact'])).toEqual([row('compact')])
  })
})
