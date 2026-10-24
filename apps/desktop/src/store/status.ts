/**
 * The dot a row of the sidebar shows, in its three tabs (asked for by the user on 20/10/2026, where
 * only words said it, and only for sessions): working, waiting for the user, done and not yet seen
 * (a blue dot), or failed. A row with nothing new has none. A row closed over others (a
 * folder, an account) shows the most urgent of what it holds.
 */
export type Dot = '' | 'failed' | 'unread' | 'waiting' | 'working'

/** What a row not on screen keeps from an end the user did not see, until it is opened. */
export type Mark = 'failed' | 'unread'

/** Most urgent first. */
const URGENCY: Dot[] = ['failed', 'waiting', 'unread', 'working']

export function worst(dots: Iterable<Dot>): Dot {
  let found = URGENCY.length

  for (const dot of dots) {
    const rank = URGENCY.indexOf(dot)

    if (rank !== -1 && rank < found) {
      found = rank
    }
  }

  return URGENCY[found] ?? ''
}

/** What a turn's end leaves on a row not on screen, as `conversations.changed` says it ended: the
 * user's own stop leaves nothing. */
export function ended(status: string): Mark | null {
  if (status === 'interrupted') {
    return null
  }

  return status === 'failed' || status === 'limited' || status === 'halted' ? 'failed' : 'unread'
}

/** A conversation's dot: a failure stays until it is opened, an answer asked for comes next. */
export function conversationDot(live: boolean, asks: boolean, mark: Mark | undefined): Dot {
  if (mark === 'failed') {
    return 'failed'
  }

  if (asks) {
    return 'waiting'
  }

  return live ? 'working' : (mark ?? '')
}

/** An executor's instance's dot, from its state, what it asks, and what it left unseen. */
export function instanceDot(state: string, asks: boolean, mark: Mark | undefined): Dot {
  if (state === 'failed') {
    return 'failed'
  }

  if (asks) {
    return 'waiting'
  }

  return state === 'working' || state === 'starting' ? 'working' : (mark ?? '')
}

/** Where an instance's mark and last use are kept, apart from the conversations' ids. */
export function instanceKey(instanceId: string): string {
  return `instance\u0000${instanceId}`
}

/** A row's last use: its own time, or a later moment this app saw it start to work. */
export function lastUse(utc: null | string | undefined, seen: number | undefined): number {
  const at = utc ? Date.parse(utc) : NaN

  return Math.max(Number.isNaN(at) ? 0 : at, seen ?? 0)
}

/** The rows, the last used first; rows used at the same time keep their order. */
export function byLastUse<T>(rows: readonly T[], at: (row: T) => number): T[] {
  return rows
    .map((row, index) => ({ at: at(row), index, row }))
    .sort((a, b) => b.at - a.at || a.index - b.index)
    .map(({ row }) => row)
}
