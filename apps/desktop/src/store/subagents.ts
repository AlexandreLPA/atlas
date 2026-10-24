/**
 * A conversation's running subagents, as its agent map shows them (goal, last
 * tool, time). Pure: the server's `subagent.update` events and `subagent.list`
 * answers keep it, and a subagent that ended leaves the map, since its batch's notice reaches the
 * conversation.
 */
import type { SubagentRow } from '@/protocol/protocol.generated'

/** The map after one update: a running subagent is added or replaced, an ended one leaves. */
export function applySubagent(rows: readonly SubagentRow[], row: SubagentRow): SubagentRow[] {
  const others = rows.filter(kept => kept.subagent_id !== row.subagent_id)

  if (row.status !== 'running') {
    return others
  }

  const index = rows.findIndex(kept => kept.subagent_id === row.subagent_id)

  if (index < 0) {
    return [...others, row]
  }

  return [...rows.slice(0, index), row, ...rows.slice(index + 1)]
}

/** How long a subagent has run, as `45s`, `3m 05s` or `1h 02m`. */
export function elapsed(startedAt: string, now: number): string {
  const seconds = Math.max(0, Math.floor((now - Date.parse(startedAt)) / 1000))

  if (seconds < 60) {
    return `${seconds}s`
  }

  const minutes = Math.floor(seconds / 60)

  if (minutes < 60) {
    return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`
  }

  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`
}
