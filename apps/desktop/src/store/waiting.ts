/**
 * The tool calls a card holds (`waiting call`, docs/GLOSSARY.md): an approval names the call it
 * holds (`call_id`); a question is its `ask_user` call and a key its `secrets` call; an executor's
 * permission or question is the call of its session that asked. Such a call has not run, or waits
 * on the user to go on: the trail shows it amber with what it waits for, never as running (the
 * ACP's `pending`). Pure, from the open cards; nothing of it is stored.
 */
import type { Card } from './app'
import type { Item } from './thread'

/** What a call waits for: its approval, the user's answers, or a key. */
export type Waiting = 'answer' | 'approval' | 'key'

type Tool = Extract<Item, { kind: 'tool' }>

/** The running calls of `items` that `cards` hold, by call id. */
export function waitingCalls(cards: readonly Card[], items: readonly Item[]): ReadonlyMap<string, Waiting> {
  const running = items.filter((item): item is Tool => item.kind === 'tool' && item.status === 'running')
  const waiting = new Map<string, Waiting>()
  // The last running call with that name; any name when none is given.
  const last = (name?: string) => running.findLast(call => name === undefined || call.name === name)
  const hold = (call: Tool | undefined, why: Waiting) => {
    if (call) {
      waiting.set(call.toolId, why)
    }
  }

  for (const card of cards) {
    switch (card.kind) {
      case 'approval':
        // A server older than the request's `call_id` names only the tool.
        hold(
          card.params.call_id ? running.find(call => call.toolId === card.params.call_id) : last(card.params.tool),
          'approval'
        )
        break
      case 'ask':
        hold(last('ask_user'), 'answer')
        break
      case 'secret':
        hold(last('secrets'), 'key')
        break
      case 'executor-approval':
        hold(last(card.params.tool) ?? last(), 'approval')
        break
      case 'executor-question':
        hold(last(), 'answer')
        break
    }
  }

  return waiting
}
