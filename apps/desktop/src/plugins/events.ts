/**
 * The server's events, as the plugins' screens hear them (`ctx.onEvent`, step 16m): the home on
 * screen's, each by its type (`'*'` for all). Kept apart from the app's store so it imports
 * nothing of it.
 */
import type { EventParams } from '@/protocol/channel'

export type EventListener = (event: EventParams) => void

const listeners = new Map<string, Set<EventListener>>()

/** Hear `type` (`'*'` for every event); returns what stops hearing. */
export function onPluginEvent(type: string, listener: EventListener): () => void {
  const heard = listeners.get(type) ?? new Set<EventListener>()
  heard.add(listener)
  listeners.set(type, heard)

  return () => {
    heard.delete(listener)
  }
}

/** An event of the home on screen, handed to whoever listens; one that throws is logged. */
export function emitPluginEvent(event: EventParams): void {
  for (const listener of [...(listeners.get(event.type) ?? []), ...(listeners.get('*') ?? [])]) {
    try {
      listener(event)
    } catch (error) {
      console.error(`A plugin's listener for ${event.type} failed.`, error)
    }
  }
}
