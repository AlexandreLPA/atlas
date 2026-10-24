/**
 * The machines (step 15, docs/ARCHITECTURE.md, Several machines): the list the main process keeps,
 * this machine's home always on and open, the settings screen's Machines, which holds both.
 * The window never holds a token: the code goes to the
 * clipboard from the main process.
 */
import { atom } from 'nanostores'

import { t } from '@/i18n'

import { $home, $remote, forgetMachine, nameMachinesBy, redialHome } from './app'

/** The other machines, as the main process keeps them. */
export const $machines = atom<MachineRow[]>([])
/** This machine's home on screen in Settings: whether it is always on and open, and its tailnet. */
export const $thisMachine = atom<AlwaysOnStatus | null>(null)
/** What the last action in Settings said, until another one: a done thing, or why not. */
export const $machinesNotice = atom<null | { kind: 'done' | 'failed'; text: string }>(null)
/** An action of this machine's switches runs: they wait for it. */
export const $machinesWorking = atom(false)

/** The home of this machine that Settings shows: the one on screen, or this machine's last. */
let localHome: null | string = null

export function startMachines(): () => void {
  nameMachinesBy(machineName)
  void refreshMachines()

  return $home.subscribe(home => {
    if (!$remote.get()) {
      localHome = home
    }
  })
}

export async function refreshMachines(): Promise<void> {
  $machines.set(await window.atlas.machines.list())
}

export async function refreshThisMachine(): Promise<void> {
  const read = await window.atlas.thisMachine.status(localHome)
  $thisMachine.set(read.ok ? read.value : null)

  if (!read.ok) {
    $machinesNotice.set({ kind: 'failed', text: read.error })
  }
}

/** Turn this machine's home always on or off, open or closed; the window dials its server again. */
export async function setThisMachine(change: 'close' | 'off' | 'on' | 'open'): Promise<void> {
  await working(async () => {
    const done = await window.atlas.thisMachine.set(localHome, change)

    if (!done.ok) {
      $machinesNotice.set({ kind: 'failed', text: done.error })
    }

    redialHome(localHome)
    await refreshThisMachine()
  })
}

export async function copyCode(): Promise<void> {
  const done = await window.atlas.thisMachine.copyCode(localHome)
  $machinesNotice.set(done.ok ? { kind: 'done', text: t.machines.codeCopied } : { kind: 'failed', text: done.error })
}

export async function newToken(): Promise<void> {
  await working(async () => {
    const done = await window.atlas.thisMachine.newToken(localHome)
    $machinesNotice.set(
      done.ok ? { kind: 'done', text: t.machines.newTokenDone } : { kind: 'failed', text: done.error }
    )
    redialHome(localHome)
  })
}

/** Add a machine from its code; says why not, or `null` once added. */
export async function addMachine(given: {
  code: string
  name: string
  sshHost?: string
  transport: Transport
}): Promise<null | string> {
  const added = await window.atlas.machines.add(given)

  if (!added.ok) {
    return added.error
  }

  await refreshMachines()

  return null
}

export async function renameMachine(id: string, name: string): Promise<null | string> {
  const renamed = await window.atlas.machines.rename(id, name)
  await refreshMachines()

  return renamed.ok ? null : renamed.error
}

export async function removeMachine(id: string): Promise<void> {
  await forgetMachine(id)
  const removed = await window.atlas.machines.remove(id)

  if (!removed.ok) {
    $machinesNotice.set({ kind: 'failed', text: removed.error })
  }

  await refreshMachines()
}

/** Test a machine's two legs; says why not, or `null` when it is reachable. */
export async function testMachine(id: string): Promise<null | string> {
  const tested = await window.atlas.machines.test(id)

  return tested.ok ? null : tested.error
}

/** The machine on screen's name: this computer's, or the list's. */
export function machineName(id: null | string): string {
  return id === null
    ? t.machines.thisComputer
    : ($machines.get().find(machine => machine.id === id)?.name ?? t.machines.thisComputer)
}

async function working(work: () => Promise<void>): Promise<void> {
  $machinesWorking.set(true)
  $machinesNotice.set(null)

  try {
    await work()
  } finally {
    $machinesWorking.set(false)
  }
}
