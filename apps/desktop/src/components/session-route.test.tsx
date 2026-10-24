import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { AtlasClient } from '@/protocol/client'
import type { ExecutorRow, InstanceRow, SessionRouteRow } from '@/protocol/protocol.generated'
import { current, openHome, startApp } from '@/store/app'
import { applyActivity } from '@/store/session'
import { EMPTY_THREAD } from '@/store/thread'

import { SessionView } from './session-view'

const ROW: ExecutorRow = {
  account: 'someone@example.com',
  account_folders: true,
  account_id: '',
  accounts: true,
  base: '',
  consent: '',
  declaration: [],
  enabled: true,
  files: false,
  installed: true,
  logged_in: true,
  minimum: '',
  name: 'claude-code',
  nickname: '',
  plan: '',
  plugin: 'claude-code',
  problem: '',
  supported: true,
  title: 'Claude Code',
  version: '2.1.140'
}
const WORK: ExecutorRow = {
  ...ROW,
  account_id: 'ab12cd34',
  base: 'claude-code',
  name: 'claude-code@ab12cd34',
  nickname: 'work',
  title: 'Claude Code · work'
}
const CODEX: ExecutorRow = { ...ROW, name: 'codex', plugin: 'codex', title: 'Codex' }
const OFF: ExecutorRow = { ...ROW, enabled: false, name: 'pi', plugin: 'pi', title: 'Pi' }

const INSTANCE: InstanceRow = {
  account: 'someone@example.com',
  account_id: '',
  accounts: true,
  atlas_answers: false,
  auto: true,
  can_auto: true,
  can_model: false,
  chosen_model: '',
  commands: [],
  effort: '',
  executor: 'claude-code',
  folder: 'C:\\work\\site',
  instance_id: 'in-1',
  last_tool: null,
  model: '',
  owner: 'user',
  parent_id: '',
  problem: '',
  session_id: '',
  state: 'idle',
  steer: true,
  subagent_id: '',
  title: 'Claude Code',
  tools: 0,
  transcript_id: 'c-1',
  waiting: 0
}

const route = (stops: SessionRouteRow['stops'], enabled = true): SessionRouteRow => ({
  enabled,
  instance_id: 'in-1',
  stops
})
const MAIN_STOP = {
  account_id: '',
  executor: 'claude-code',
  installed: true,
  name: 'claude-code',
  nickname: '',
  title: 'Claude Code'
}
const CODEX_STOP = { account_id: '', executor: 'codex', installed: true, name: 'codex', nickname: '', title: 'Codex' }

let stop: () => void = () => undefined

afterEach(() => {
  cleanup()
  stop()
})
beforeAll(() => {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => undefined
})

/** The session on the instance `in-1` open, on a client answering the route's calls. */
async function openSessionOn(answer: (method: string, params: unknown) => unknown): Promise<[string, unknown][]> {
  const calls: [string, unknown][] = []
  const none = () => () => undefined
  const client = {
    call: (method: string, params: unknown) => {
      calls.push([method, params])
      const found = answer(method, params)

      return found === undefined ? new Promise(() => undefined) : Promise.resolve(found)
    },
    onError: none,
    onEvent: none,
    onRequest: none,
    onResync: none,
    onState: none,
    start: vi.fn(),
    stop: vi.fn(),
    watch: vi.fn()
  } as unknown as AtlasClient
  stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
  await openHome('work')
  const session = current()
  session.$connection.set({ error: null, state: 'open' })
  session.$workbench.set({ executors: [ROW, WORK, CODEX, OFF], instances: [INSTANCE] })
  session.$openSession.set({
    choice: null,
    elsewhere: '',
    executor: 'claude-code',
    expired: false,
    folder: 'C:\\work\\site',
    instanceId: 'in-1',
    sessionId: '',
    title: 'The site'
  })
  render(<SessionView />)

  return calls
}

const listed = (calls: [string, unknown][], method: string) => calls.filter(([called]) => called === method)

describe("a session's route across executors (asked for by the user on 21/10/2026)", () => {
  it('reads the route, adds a destination of another harness, orders it and turns it off', async () => {
    let kept = route([MAIN_STOP])
    const calls = await openSessionOn((method, params) => {
      if (method === 'workbench.route') {
        return kept
      }

      if (method === 'workbench.set_route') {
        const given = params as { enabled: boolean; targets: { account_id: string; executor: string }[] }
        const stops = given.targets.map(target => (target.executor === 'codex' ? CODEX_STOP : MAIN_STOP))
        kept = route(stops, given.enabled)

        return kept
      }

      return undefined
    })

    const pill = await screen.findByTestId('session-route')
    await vi.waitFor(() => {
      expect(pill.textContent).toBe('Route · 1')
    })
    fireEvent.click(pill)
    const editor = screen.getByTestId('session-route-editor')
    expect(
      within(editor)
        .getAllByTestId('session-route-stop')
        .map(row => row.textContent)
    ).toEqual(['1.Claude Code · main account'])
    const others = within(screen.getByTestId('session-route-others'))
      .getAllByRole('checkbox')
      .map(box => box.getAttribute('aria-label'))
    expect(others).toEqual(['Add Claude Code · work to the route', 'Add Codex · main account to the route'])

    fireEvent.click(screen.getByRole('checkbox', { name: 'Add Codex · main account to the route' }))
    await vi.waitFor(() => {
      expect(screen.getAllByTestId('session-route-stop')).toHaveLength(2)
    })
    expect(listed(calls, 'workbench.set_route')[0]).toEqual([
      'workbench.set_route',
      {
        enabled: true,
        instance_id: 'in-1',
        targets: [
          { account_id: '', executor: 'claude-code' },
          { account_id: '', executor: 'codex' }
        ]
      }
    ])

    fireEvent.click(screen.getByRole('button', { name: 'Codex · main account sooner' }))
    await vi.waitFor(() => {
      expect(screen.getAllByTestId('session-route-stop').map(row => row.textContent)).toEqual([
        '1.Codex · main account',
        '2.Claude Code · main account'
      ])
    })
    expect(screen.getByTestId('session-route').textContent).toBe('Route · 2')

    fireEvent.click(screen.getByTestId('session-route-enabled'))
    await vi.waitFor(() => {
      expect(listed(calls, 'workbench.set_route')).toHaveLength(3)
    })
    expect(listed(calls, 'workbench.set_route')[2]?.[1]).toMatchObject({ enabled: false })
    await vi.waitFor(() => {
      expect(screen.getByTestId('session-route').textContent).toBe('Route')
    })
  })

  it('closes on a click outside it, on Escape and on its own button (asked for on 22/10/2026)', async () => {
    await openSessionOn(method => (method === 'workbench.route' ? route([MAIN_STOP]) : undefined))
    const pill = await screen.findByTestId('session-route')

    fireEvent.click(pill)
    fireEvent.pointerDown(screen.getByTestId('session-route-editor'))
    expect(screen.getByTestId('session-route-panel')).toBeTruthy()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByTestId('session-route-panel')).toBeNull()

    fireEvent.click(pill)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByTestId('session-route-panel')).toBeNull()

    fireEvent.click(pill)
    fireEvent.click(screen.getByTestId('session-route-close'))
    expect(screen.queryByTestId('session-route-panel')).toBeNull()

    fireEvent.pointerDown(pill)
    fireEvent.click(pill)
    expect(screen.getByTestId('session-route-panel')).toBeTruthy()
    fireEvent.pointerDown(pill)
    fireEvent.click(pill)
    expect(screen.queryByTestId('session-route-panel')).toBeNull()
  })

  it('says on the session where it went on, and why it could not', () => {
    const moved = applyActivity(
      EMPTY_THREAD,
      {
        attachments: [],
        call_id: '',
        failed: false,
        instance_id: 'in-1',
        kind: 'route',
        text: 'Codex · main account',
        tool: ''
      },
      1,
      0
    )
    const stuck = applyActivity(
      moved,
      {
        attachments: [],
        call_id: '',
        failed: true,
        instance_id: 'in-1',
        kind: 'route',
        text: "No destination in the session's route can go on now.",
        tool: ''
      },
      2,
      0
    )

    expect(stuck.items).toEqual([
      { key: 'a1', kind: 'status', text: 'The plan limit was reached: it went on on Codex · main account.' },
      { key: 'a2', kind: 'status', text: "No destination in the session's route can go on now." }
    ])
  })
})
