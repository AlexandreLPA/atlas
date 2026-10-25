import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { host } from '@/plugins/sdk'
import type { AtlasClient, ConnectionState } from '@/protocol/client'
import type { UpdateEnded, UpdateInfo, UpdateProgress } from '@/protocol/protocol.generated'
import {
  $confirmUnverified,
  $update,
  $updateEnded,
  $updateError,
  $updateLog,
  $updateOpen,
  $updating,
  applyUpdate,
  followScreen,
  openUpdateLog,
  serverOnScreen
} from '@/store/updates'

import { UpdateBar } from './update-bar'

/** The same update button, with a Compose host's server on screen (docs/ARCHITECTURE.md, The
 * update of a Compose host): its looks and its update go through the server's `update.*` methods,
 * the server restarts, the app reconnects and says how it ended, once. */

const TARGET = 'b'.repeat(40)

const SERVER: UpdateInfo = {
  available: true,
  behind: 2,
  commits: ['The voice plugin', 'A plugin secret already stored'],
  commits_pt_br: ['', ''],
  current: 'a'.repeat(40),
  dirty: false,
  error: '',
  kind: 'compose',
  reason: '',
  supported: true,
  target: TARGET,
  unverified: ''
}

function ended(overrides: Partial<UpdateEnded> = {}): UpdateEnded {
  return {
    after: TARGET,
    before: 'a'.repeat(40),
    commits: ['bbbbbbb The voice plugin', 'bbbbbbb A plugin secret already stored'],
    ended_at: '2026-10-24T20:00:00.000Z',
    error: '',
    log: '/var/lib/atlas-updater/update.log',
    ok: true,
    reopen: '',
    ...overrides
  }
}

type Answer = (params: unknown) => unknown

/** A client of the server on screen, answering each method as the test says, the way the protocol
 * would; one it does not answer is one the server lacks. */
function server(answers: Record<string, Answer>) {
  const events = new Set<(event: { payload?: unknown; type: string }) => void>()
  const states = new Set<(state: ConnectionState) => void>()
  const calls: [string, unknown][] = []
  const client = {
    call: vi.fn((method: string, params: unknown) => {
      calls.push([method, params])
      const answer = answers[method]

      return answer
        ? Promise.resolve().then(() => answer(params))
        : Promise.reject(new Error(`There is no method '${method}'.`))
    }),
    onEvent: (listener: (event: { payload?: unknown; type: string }) => void) => {
      events.add(listener)

      return () => events.delete(listener)
    },
    onState: (listener: (state: ConnectionState) => void) => {
      states.add(listener)

      return () => states.delete(listener)
    }
  }

  return {
    answers,
    calls,
    client: client as unknown as AtlasClient,
    emit: (type: string, payload: unknown) => {
      for (const listener of events) {
        listener({ payload, type })
      }
    },
    state: (state: ConnectionState) => {
      for (const listener of states) {
        listener(state)
      }
    }
  }
}

async function settle(): Promise<void> {
  await act(async () => {
    for (let round = 0; round < 5; round += 1) {
      await Promise.resolve()
    }
  })
}

afterEach(() => {
  followScreen(null)
  cleanup()
  $update.set(null)
  $updateEnded.set(null)
  $updateError.set(null)
  $updateLog.set(null)
  $updateOpen.set(false)
  $updating.set(false)
  $confirmUnverified.set(false)
})

describe("a server's update, through the same button", () => {
  it("shows the server's look while it is on screen, and this computer's again after", async () => {
    const local: UpdateStatus = { ...SERVER, behind: 5, kind: 'native', target: 'c'.repeat(40) }
    $update.set(local)
    const remote = server({ 'update.check': () => SERVER })

    followScreen(remote.client)
    await settle()
    render(<UpdateBar />)
    expect(screen.getByTestId('update-count').textContent).toBe('2')

    act(() => {
      followScreen(null)
    })
    expect(screen.getByTestId('update-count').textContent).toBe('5')
    expect(remote.calls.map(([method]) => method)).toEqual(['update.check'])
  })

  it('offers nothing for a server from before these methods', async () => {
    const old = server({})

    followScreen(old.client)
    await settle()
    render(<UpdateBar />)

    expect(screen.queryByTestId('update-button')).toBeNull()
  })

  it('asks the server to update, follows it through the restart, and says once how it ended', async () => {
    let progress: UpdateProgress = { ended: null, running: false, started_at: '', step: '', target: '' }
    const remote = server({
      'update.apply': () => ({ error: '', ok: true }),
      'update.check': () => SERVER,
      'update.status': () => progress
    })
    followScreen(remote.client)
    await settle()
    render(<UpdateBar />)
    fireEvent.click(screen.getByTestId('update-button'))

    const dialog = screen.getByTestId('update-dialog')
    expect(dialog.textContent).toContain('restarts for a moment')
    expect(screen.getByTestId('update-apply').textContent).toBe('Update and reconnect')
    await act(async () => {
      await applyUpdate()
    })

    expect(remote.calls).toContainEqual(['update.apply', { allow_unverified: false, target: TARGET }])
    expect(screen.getByTestId('update-apply').textContent).toBe('The server is updating…')
    expect(screen.getByTestId('update-apply').hasAttribute('disabled')).toBe(true)

    // The server restarts: its calls fail meanwhile, and nothing is said.
    remote.answers['update.status'] = () => {
      throw new Error('The connection closed.')
    }
    await act(async () => {
      await serverOnScreen()?.follow()
    })
    expect($updating.get()).toBe(true)
    expect(screen.queryByTestId('update-ended')).toBeNull()

    // Back, on the new version: the reconnection asks, and the end is said.
    progress = { ...progress, ended: ended() }
    remote.answers['update.status'] = () => progress
    remote.answers['update.check'] = () => ({ ...SERVER, available: false, behind: 0 })
    remote.state('open')
    await settle()

    expect(screen.getByTestId('update-ended').textContent).toContain('Atlas was updated: 2 changes.')
    expect($updating.get()).toBe(false)
    expect($updateOpen.get()).toBe(false)
    expect(screen.queryByTestId('update-button')).toBeNull()

    // A connection that comes back is told again; it is said once.
    act(() => {
      fireEvent.click(screen.getByLabelText('Dismiss'))
    })
    remote.emit('update.ended', ended())
    await settle()
    expect(screen.queryByTestId('update-ended')).toBeNull()
  })

  it('tells the end of an update apart from the one before it, and says a failure with its log', async () => {
    const before = ended({ ended_at: '2026-10-23T10:00:00.000Z' })
    let progress: UpdateProgress = { ended: before, running: true, started_at: '', step: 'building', target: TARGET }
    const remote = server({
      'update.apply': () => ({ error: '', ok: true }),
      'update.check': () => SERVER,
      'update.log': () => ({ text: '$ docker build\nstep 7 failed\n' }),
      'update.status': () => progress
    })
    followScreen(remote.client)
    await settle()
    render(<UpdateBar />)
    await act(async () => {
      await applyUpdate()
    })

    // The last update's end is still the one before: not this one's.
    progress = { ...progress, running: false }
    await act(async () => {
      await serverOnScreen()?.follow()
    })
    expect(screen.queryByTestId('update-ended')).toBeNull()

    const failed = ended({
      after: '',
      commits: [],
      ended_at: '2026-10-24T21:00:00.000Z',
      error: 'The new container calls itself unhealthy. The server is back on aaaaaaaaaaaa and /data as it was.',
      ok: false
    })
    progress = { ...progress, ended: failed }
    await act(async () => {
      await serverOnScreen()?.follow()
    })

    expect(screen.getByRole('alert').textContent).toContain('back on aaaaaaaaaaaa and /data as it was')
    await act(async () => {
      openUpdateLog()
      await Promise.resolve()
    })
    await settle()
    expect(screen.getByTestId('update-log').textContent).toContain('step 7 failed')
  })

  it('says why the server refused, and stays as it was', async () => {
    const remote = server({
      'update.apply': () => ({ error: 'main moved since the look: look again.', ok: false }),
      'update.check': () => SERVER,
      'update.status': () => ({ ended: null, running: false, started_at: '', step: '', target: '' })
    })
    followScreen(remote.client)
    await settle()
    $updateOpen.set(true)
    render(<UpdateBar />)

    await act(async () => {
      await applyUpdate()
    })

    expect(screen.getByRole('alert').textContent).toBe('main moved since the look: look again.')
    expect(screen.getByTestId('update-apply').textContent).toBe('Update and reconnect')
  })

  it('asks for an explicit yes to a version CI did not verify, and says it was given', async () => {
    const reason = 'GitHub Actions could not start its jobs (a billing or quota block)'
    const remote = server({
      'update.apply': () => ({ error: '', ok: true }),
      'update.check': () => ({ ...SERVER, unverified: reason }),
      'update.status': () => ({ ended: null, running: false, started_at: '', step: '', target: '' })
    })
    followScreen(remote.client)
    await settle()
    $updateOpen.set(true)
    render(<UpdateBar />)

    expect(screen.getByTestId('update-unverified').textContent).toContain(reason)
    expect(screen.getByTestId('update-apply').hasAttribute('disabled')).toBe(true)

    fireEvent.click(screen.getByTestId('update-confirm-unverified'))
    expect(screen.getByTestId('update-apply').hasAttribute('disabled')).toBe(false)
    await act(async () => {
      await applyUpdate()
    })

    expect(remote.calls).toContainEqual(['update.apply', { allow_unverified: true, target: TARGET }])
  })
})

describe("a plugin's screen", () => {
  it('cannot call the update: only the owner, from the button, updates Atlas', async () => {
    await expect(host.request('update.apply', { allow_unverified: true, target: TARGET })).rejects.toThrow(
      "A plugin's screen cannot update Atlas."
    )
    await expect(host.request('update.check', {})).rejects.toThrow("A plugin's screen cannot update Atlas.")
  })
})
