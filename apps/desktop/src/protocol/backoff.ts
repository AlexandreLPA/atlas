/**
 * Exponential reconnect backoff with full jitter.
 *
 * Full jitter keeps clients that lost the same server
 * at the same instant from redialing in lockstep (AWS, "Exponential Backoff And Jitter").
 */

export interface BackoffOptions {
  /** The first retry's ceiling, in ms. */
  baseDelayMs?: number
  /** The largest ceiling, in ms. */
  capMs?: number
  /** Full jitter (default), or the ceiling itself. */
  jitter?: boolean
}

const DEFAULT_BASE_DELAY_MS = 300
const DEFAULT_CAP_MS = 15_000
/** Past this many doublings the ceiling stops growing, and `2 **` stays a finite number. */
const LAST_DOUBLING = 32

/**
 * How long a socket must stay open to count as a success. One that drops sooner is one more failed
 * attempt: were every brief open a fresh start, a server that accepts and closes at once would be
 * redialed at the shortest wait forever.
 */
export const STABLE_OPEN_MS = 5_000

export function isStableOpen(openedAt: null | number, now = Date.now()): boolean {
  if (openedAt === null) {
    return false
  }

  return now - openedAt >= STABLE_OPEN_MS
}

/** The wait before retry number `attempt` (0 first): within `[0, min(cap, base * 2 ** attempt))`. */
export function backoffDelayMs(attempt: number, options: BackoffOptions = {}): number {
  const doublings = Math.min(LAST_DOUBLING, Math.max(Math.trunc(attempt), 0))
  const firstWait = options.baseDelayMs ?? DEFAULT_BASE_DELAY_MS
  const longestWait = options.capMs ?? DEFAULT_CAP_MS
  const ceiling = Math.min(firstWait * 2 ** doublings, longestWait)

  if (options.jitter === false) {
    return ceiling
  }

  return ceiling * Math.random()
}
