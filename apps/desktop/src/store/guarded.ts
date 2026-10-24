import { batch } from 'nanostores'

/**
 * Runs a change to the stores so that one listener that throws cannot silence them all.
 *
 * Nano Stores (1.4.2) stops draining its queue of listeners when one throws, and never drains it
 * again: every store goes on changing without telling the screen, which looks frozen while its own
 * timers still tick. React threw this way, from inside a store's listener, when a fast stream passed
 * its limit of nested updates. An empty `batch` drains what was left in the queue.
 */
export function guarded(change: () => void): void {
  try {
    change()
  } catch (error) {
    console.error('A change to the stores failed; their listeners go on.', error)

    // A listener left in the queue may throw too: drain again, a few times at most.
    for (let tries = 0; tries < 3; tries++) {
      try {
        batch(() => undefined)

        return
      } catch (again) {
        console.error('A store listener failed again.', again)
      }
    }
  }
}
