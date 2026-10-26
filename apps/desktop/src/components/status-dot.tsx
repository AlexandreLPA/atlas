import { useStore } from '@nanostores/react'

import { t } from '@/i18n'
import type { InstanceRow, SessionRow } from '@/protocol/protocol.generated'
import { $cards, $live, $marks, $workbench, type Card } from '@/store/app'
import { conversationDot, type Dot, instanceDot, instanceKey, worst } from '@/store/status'

// One color for each state, as on the trail (decided with the user on 25/10/2026): blue and
// pulsing at work, amber waiting for the user, red failed; unseen, a plain dot.
const COLOR: Record<Exclude<Dot, ''>, string> = {
  failed: 'bg-failed',
  unread: 'bg-foreground',
  waiting: 'bg-waiting',
  working: 'bg-working motion-safe:animate-pulse'
}

/** The dot a row shows: a row says it is unseen with its title in bold instead (decided with the
 * user on 25/10/2026), and the dot is left to what gathers rows, a tab or a closed folder. */
export function rowDot(dot: Dot): Dot {
  return dot === 'unread' ? '' : dot
}

/**
 * A row's state as a dot, named on hover (store/status.ts). `slot` keeps its place when there is
 * none, so the titles of a list start at the same edge; `ring` draws it over a picture's corner.
 */
export function StatusDot({ dot, ring = false, slot = false }: { dot: Dot; ring?: boolean; slot?: boolean }) {
  if (!dot) {
    return slot ? <span aria-hidden className="h-2 w-2 shrink-0" /> : null
  }

  return (
    <span
      aria-label={t.status[dot]}
      className={`block h-2 w-2 shrink-0 rounded-full ${COLOR[dot]} ${
        ring ? 'absolute -right-0.5 -bottom-0.5 box-content border-2 border-subtle' : ''
      }`}
      data-dot={dot}
      data-testid="status-dot"
      role="img"
      title={t.status[dot]}
    />
  )
}

/** A card of the conversation, or of an instance, waits for the user's answer. */
function asksIn(cards: Card[], conversationId: string): boolean {
  return cards.some(card => card.conversationId === conversationId && conversationId !== '')
}

function asksOf(cards: Card[], instanceId: string): boolean {
  return cards.some(
    card =>
      (card.kind === 'executor-approval' || card.kind === 'executor-question') &&
      instanceId !== '' &&
      card.params.instance_id === instanceId
  )
}

/** A conversation's dot, a bot's too: its turn, what it asks, what it ended with unseen. */
export function useConversationDot(conversationId: null | string): Dot {
  const live = useStore($live)
  const marks = useStore($marks)
  const cards = useStore($cards)

  return conversationId ? (conversationDots([conversationId], live, marks, cards)[0] ?? '') : ''
}

/** The most urgent of several conversations' dots, for their tab. */
export function useConversationsDot(conversationIds: (null | string)[]): Dot {
  const live = useStore($live)
  const marks = useStore($marks)
  const cards = useStore($cards)
  const ids = conversationIds.filter((id): id is string => Boolean(id))

  return worst(conversationDots(ids, live, marks, cards))
}

function conversationDots(
  ids: string[],
  live: Record<string, true | undefined>,
  marks: ReturnType<typeof $marks.get>,
  cards: Card[]
): Dot[] {
  return ids.map(id => conversationDot(live[id] === true, asksIn(cards, id), marks[id]))
}

/** What tells a session: its id in its harness, and its instance here (a row's, or a tab's). */
type SessionIds = Pick<SessionRow, 'instance_id' | 'session_id'>

/** A session's dot, from its instance here as its events tell it, not as the list was last read
 * (asked for by the user on 22/10/2026: a session at work showed nothing until its folder was read
 * again, nor one whose instance began after it was). */
export function useSessionDot(row: SessionIds): Dot {
  const live = useSessionInstance(row)
  const marks = useStore($marks)
  const cards = useStore($cards)
  const instanceId = live?.instance_id ?? row.instance_id

  if (!instanceId) {
    return ''
  }

  const asks = (live?.waiting ?? 0) > 0 || asksOf(cards, instanceId)

  return instanceDot(live?.state ?? '', asks, marks[instanceKey(instanceId)])
}

/** The instance on a session now, not ended: the one its row was read with, or one begun on it
 * since. */
export function useSessionInstance(row: SessionIds): InstanceRow | undefined {
  const { instances } = useStore($workbench)

  return instances.find(found =>
    row.instance_id
      ? found.instance_id === row.instance_id
      : row.session_id !== '' && found.session_id === row.session_id
  )
}

/** The most urgent dot of the instances here that `pick` keeps: an executor's, a folder's, all. */
export function useInstancesDot(pick: (row: InstanceRow) => boolean): Dot {
  const { instances } = useStore($workbench)
  const marks = useStore($marks)
  const cards = useStore($cards)

  return worst(
    instances
      .filter(row => row.state !== 'ended' && pick(row))
      .map(row =>
        instanceDot(row.state, row.waiting > 0 || asksOf(cards, row.instance_id), marks[instanceKey(row.instance_id)])
      )
  )
}
