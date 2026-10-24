/**
 * A conversation's background commands, as its agent map lists them. Pure: the server's `command.update` events and `command.list` answers keep it. An ended
 * command stays, so a map open while it ends still shows how it ended and its output; the newest
 * `ENDED_KEPT` ended ones are kept, as the server keeps their output.
 */
import type { CommandRow } from '@/protocol/protocol.generated'

const ENDED_KEPT = 20

/** The list after one update: a command is added or replaced in place, the oldest ended ones go. */
export function applyCommand(rows: readonly CommandRow[], row: CommandRow): CommandRow[] {
  const index = rows.findIndex(kept => kept.command_id === row.command_id)
  const next = index < 0 ? [...rows, row] : [...rows.slice(0, index), row, ...rows.slice(index + 1)]
  const ended = next.filter(kept => kept.status !== 'running')
  const dropped = new Set(ended.slice(0, Math.max(0, ended.length - ENDED_KEPT)))

  return next.filter(kept => !dropped.has(kept))
}

/** How the map names a command: the model's description, or its command's first line. */
export function commandName(row: CommandRow): string {
  return row.description || row.command.split('\n')[0]?.trim() || row.command_id
}
