import { atom } from 'nanostores'

import type { AtlasClient } from '@/protocol/client'
import type { UpdateEnded } from '@/protocol/protocol.generated'

import { $remote, $session } from './app'

/**
 * The app's update on screen (electron/updates.ts, asked for by the user on 18/10/2026): what the
 * last look found, which shows the button; the hand-off asked for, after which the app closes and
 * opens again by itself; and how the last update ended, said once after it did.
 *
 * **The same button follows the machine on screen** (docs/ARCHITECTURE.md, The update of a Compose
 * host): this computer's installation through the main process, or the server's, another machine's
 * or a container's, through its own `update.*` methods. A Compose host's update is built while its
 * server runs, which then restarts: the app reconnects by itself, asks `update.status` until it
 * says how the update ended, and shows it as this computer's own ends are shown.
 */
export const $update = atom<null | UpdateStatus>(null)
export const $updateEnded = atom<HandedOff | null>(null)
/** The hand-off was asked for, and the app is about to close; or the server updates. */
export const $updating = atom(false)
/** Why the hand-off did not start; the app stays open. */
export const $updateError = atom<null | string>(null)
/** The dialog that says what the update brings, opened from the sidebar or from its strip. */
export const $updateOpen = atom(false)
/** The user confirmed an update CI did not verify (a server's only). */
export const $confirmUnverified = atom(false)
/** A server's last update's log, once asked for: it lives on that machine. */
export const $updateLog = atom<null | string>(null)

/** How often a server's update is asked how it goes, and how long before the app gives up. */
export const FOLLOW_EVERY_MS = 3_000
export const GIVE_UP_MS = 45 * 60_000
/** How long a server's look is reused, as this computer's (electron/updates.ts). */
export const CHECK_EVERY_MS = 60 * 60_000

/** What the update bar shows for one target: this computer, or a server. */
interface Shown {
  ended: HandedOff | null
  error: null | string
  log: null | string
  status: null | UpdateStatus
  updating: boolean
}

const nothing = (): Shown => ({ ended: null, error: null, log: null, status: null, updating: false })

/** This computer's, kept while a server is on screen. */
let local: Shown = nothing()
/** The server on screen, or `null` for this computer. */
let onScreen: null | ServerUpdate = null
const servers = new WeakMap<object, ServerUpdate>()

function current(): Shown {
  return {
    ended: $updateEnded.get(),
    error: $updateError.get(),
    log: $updateLog.get(),
    status: $update.get(),
    updating: $updating.get()
  }
}

function show(shown: Shown): void {
  $update.set(shown.status)
  $updateEnded.set(shown.ended)
  $updateError.set(shown.error)
  $updateLog.set(shown.log)
  $updating.set(shown.updating)
}

/** This computer's look or end, shown when it is on screen, kept otherwise. */
function setLocal(change: Partial<Shown>): void {
  if (onScreen === null) {
    show({ ...current(), ...change })
  } else {
    local = { ...local, ...change }
  }
}

/** The update of a server the app reaches: its looks, its update and how it ended. */
export class ServerUpdate {
  shown: Shown = nothing()
  private checkedAt = 0
  /** An update asked for: the end the server said before it, so a new one is told apart. */
  private awaiting: null | { before: string; since: number } = null
  private timer: null | ReturnType<typeof setInterval> = null
  /** The ends already shown, by when they ended: a reconnection tells one again. */
  private readonly seen = new Set<string>()

  constructor(readonly client: AtlasClient) {
    client.onEvent(event => {
      if (event.type === 'update.ended' && event.payload) {
        this.ended(event.payload as UpdateEnded)
      }
    })
    client.onState(state => {
      if (state === 'open' && this.awaiting) {
        // The server restarted: it may already say how the update went.
        void this.follow()
      }
    })
  }

  set(change: Partial<Shown>): void {
    this.shown = { ...this.shown, ...change }

    if (onScreen === this) {
      show(this.shown)
    }
  }

  /** Ask the server whether it has an update; a server from before these methods has none. */
  async look(now = false): Promise<void> {
    if (!now && this.shown.status && Date.now() - this.checkedAt < CHECK_EVERY_MS) {
      return
    }

    this.checkedAt = Date.now()

    try {
      this.set({ status: await this.client.call('update.check', {}, 0) })
    } catch {
      this.set({ status: null })
    }
  }

  /** Ask the server to update to the commit on screen; its host builds it, then restarts it. */
  async apply(allowUnverified: boolean): Promise<void> {
    const status = this.shown.status

    if (!status?.available) {
      return
    }

    this.set({ error: null, updating: true })
    let before = ''

    try {
      before = (await this.client.call('update.status', {})).ended?.ended_at ?? ''
    } catch {
      // Said by the answer to the update itself.
    }

    try {
      const { error, ok } = await this.client.call(
        'update.apply',
        { allow_unverified: allowUnverified, target: status.target },
        0
      )

      if (!ok) {
        this.set({ error, updating: false })

        return
      }
    } catch (error) {
      this.set({ error: error instanceof Error ? error.message : String(error), updating: false })

      return
    }

    this.awaiting = { before, since: Date.now() }
    this.timer = setInterval(() => {
      void this.follow()
    }, FOLLOW_EVERY_MS)
  }

  /** Ask how the update goes; while the server restarts the call fails, and is asked again. */
  async follow(): Promise<void> {
    const awaiting = this.awaiting

    if (!awaiting) {
      return
    }

    if (Date.now() - awaiting.since > GIVE_UP_MS) {
      this.stopFollowing()
      this.set({ error: 'The server did not say how its update ended; see its log there.', updating: false })

      return
    }

    try {
      const progress = await this.client.call('update.status', {})

      if (!progress.running && progress.ended && progress.ended.ended_at !== awaiting.before) {
        this.ended(progress.ended)
      }
    } catch {
      // The server is restarting.
    }
  }

  /** How an update ended, shown once. */
  ended(ended: UpdateEnded): void {
    if (this.seen.has(ended.ended_at)) {
      return
    }

    this.seen.add(ended.ended_at)
    this.stopFollowing()
    this.set({ ended, log: null, updating: false })

    if (onScreen === this) {
      $updateOpen.set(false)
    }

    void this.look(true)
  }

  async openLog(): Promise<void> {
    try {
      this.set({ log: (await this.client.call('update.log', {})).text })
    } catch (error) {
      this.set({ log: error instanceof Error ? error.message : String(error) })
    }
  }

  private stopFollowing(): void {
    this.awaiting = null

    if (this.timer !== null) {
      clearInterval(this.timer)
      this.timer = null
    }
  }
}

/** The update of the server a client reaches, made the first time it is on screen. */
export function serverUpdate(client: AtlasClient): ServerUpdate {
  let found = servers.get(client)

  if (!found) {
    found = new ServerUpdate(client)
    servers.set(client, found)
  }

  return found
}

/** Put on screen the update of what the screen shows: a server's, or this computer's. */
export function followScreen(client: AtlasClient | null): void {
  const next = client ? serverUpdate(client) : null

  if (next === onScreen) {
    return
  }

  const leaving = current()

  if (onScreen) {
    onScreen.shown = leaving
  } else {
    local = leaving
  }

  onScreen = next
  $confirmUnverified.set(false)
  show(next ? next.shown : local)
  void next?.look()
}

/** The server on screen, or `null` for this computer. */
export function serverOnScreen(): null | ServerUpdate {
  return onScreen
}

/** Follow the main process's looks, and read how the last update ended. Returns what stops. */
export function startUpdates(bridge: UpdatesBridge = window.atlas.updates): () => void {
  const stop = bridge.onChange(status => {
    setLocal({ status })
  })
  void bridge
    .status()
    .then(status => {
      if (status) {
        setLocal({ status })
      }
    })
    .catch(() => undefined)
  void bridge
    .ended()
    .then(ended => {
      setLocal({ ended })
    })
    .catch(() => undefined)
  const follow = () => {
    const session = $session.get()
    followScreen($remote.get() && session ? session.client : null)
  }
  const unfollowSession = $session.listen(follow)
  const unfollowRemote = $remote.listen(follow)
  follow()
  // A server on screen is looked at every hour too, as this computer is by the main process.
  const hourly = setInterval(() => {
    void onScreen?.look(true)
  }, CHECK_EVERY_MS)

  return () => {
    stop()
    unfollowSession()
    unfollowRemote()
    clearInterval(hourly)
  }
}

/** Hand the update off, or ask the server on screen to update. On success this computer's app
 * closes now, and opens again once updated; a server restarts, and the app reconnects. Otherwise
 * it stays open and says why. */
export async function applyUpdate(bridge?: UpdatesBridge): Promise<void> {
  if (onScreen) {
    await onScreen.apply($confirmUnverified.get())

    return
  }

  $updating.set(true)
  $updateError.set(null)

  try {
    const { error, ok } = await (bridge ?? window.atlas.updates).apply()

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
  if (onScreen) {
    onScreen.set({ ended: null, log: null })

    return
  }

  $updateEnded.set(null)
}

export function openUpdateLog(bridge?: UpdatesBridge): void {
  if (onScreen) {
    void onScreen.openLog()

    return
  }

  void (bridge ?? window.atlas.updates).openLog().catch(() => undefined)
}
