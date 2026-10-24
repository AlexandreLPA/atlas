/**
 * What the box offers as `/` is typed (asked for by the user on 18/10/2026), the same in every
 * conversation: an executor's commands as its harness describes them, Atlas's own and its skills in
 * a conversation of Atlas's or a bot's. Matched in tiers (the name exactly, its start, inside
 * it, then a word of its description), commands before skills, at most 30 of each. Pure.
 */
import type { SlashCommandRow } from '@/protocol/protocol.generated'

/** How many of each group show (per kind). */
export const PER_GROUP = 30
/** The groups, in the order they show. */
export const GROUPS = ['command', 'skill'] as const

export type SlashGroup = (typeof GROUPS)[number]

export interface SlashGroupRows {
  group: SlashGroup
  rows: SlashCommandRow[]
}

/** The word being typed when the box holds `/` and a name, nothing else yet; `null` otherwise. */
export function slashQuery(text: string): null | string {
  const typed = /^\/(\S*)$/.exec(text)

  return typed ? (typed[1] ?? '') : null
}

/** How well a row matches: 0 its name exactly, 1 its start, 2 inside it, 3 a word of its
 * description starts so; `null` when it does not. The part after a plugin's `:` counts as a name. */
function tier(row: SlashCommandRow, query: string): null | number {
  if (!query) {
    return 1
  }

  const name = row.name.toLowerCase()
  const short = name.includes(':') ? name.slice(name.lastIndexOf(':') + 1) : name

  if (name === query || short === query) {
    return 0
  }

  if (name.startsWith(query) || short.startsWith(query)) {
    return 1
  }

  if (name.includes(query)) {
    return 2
  }

  return row.description
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .some(word => word.startsWith(query))
    ? 3
    : null
}

/** The rows that match `query`, in their groups, the best first, then as their source listed them. */
export function offered(rows: readonly SlashCommandRow[], query: string): SlashGroupRows[] {
  const wanted = query.toLowerCase()
  const ranked = rows
    .map((row, index) => ({ index, row, tier: tier(row, wanted) }))
    .filter((found): found is { index: number; row: SlashCommandRow; tier: number } => found.tier !== null)
    .sort((a, b) => a.tier - b.tier || a.index - b.index)

  return GROUPS.map(group => ({
    group,
    rows: ranked
      .filter(found => (found.row.group === 'command' ? 'command' : 'skill') === group)
      .slice(0, PER_GROUP)
      .map(found => found.row)
  })).filter(found => found.rows.length > 0)
}

/** Rows from bare names, while the described list is read (a harness's names on its card). */
export function fromNames(names: readonly string[]): SlashCommandRow[] {
  return names.map(name => ({ description: '', group: 'skill', hint: '', name }))
}
