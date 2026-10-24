import { atom } from 'nanostores'

/**
 * The app's update on screen (electron/updates.ts, asked for by the user on 18/10/2026): what the
 * last look found, which shows the button; the hand-off asked for, after which the app closes and
 * opens again by itself; and how the last update ended, said once after it did.
 */
export const $update = atom<null | UpdateStatus>(null)
export const $updateEnded = atom<HandedOff | null>(null)
/** The hand-off was asked for, and the app is about to close. */
export const $updating = atom(false)
/** Why the hand-off did not start; the app stays open. */
export const $updateError = atom<null | string>(null)
/** The dialog that says what the update brings, opened from the sidebar or from its strip. */
export const $updateOpen = atom(false)

/** Follow the main process's looks, and read how the last update ended. Returns what stops. */
export function startUpdates(bridge: UpdatesBridge = window.atlas.updates): () => void {
  const stop = bridge.onChange(status => {
    $update.set(status)
  })
  void bridge
    .status()
    .then(status => {
      if (status) {
        $update.set(status)
      }
    })
    .catch(() => undefined)
  void bridge
    .ended()
    .then(ended => {
      $updateEnded.set(ended)
    })
    .catch(() => undefined)

  return stop
}

/** Hand the update off. On success the app closes now, and opens again once updated; otherwise
 * it stays open and says why. */
export async function applyUpdate(bridge: UpdatesBridge = window.atlas.updates): Promise<void> {
  $updating.set(true)
  $updateError.set(null)

  try {
    const { error, ok } = await bridge.apply()

    if (!ok) {
      $updateError.set(error)
      $updating.set(false)
    }
  } catch (error) {
    $updateError.set(error instanceof Error ? error.message : String(error))
    $updating.set(false)
  }
}

export function dismissUpdateEnded(): void {
  $updateEnded.set(null)
}

export function openUpdateLog(bridge: UpdatesBridge = window.atlas.updates): void {
  void bridge.openLog().catch(() => undefined)
}
