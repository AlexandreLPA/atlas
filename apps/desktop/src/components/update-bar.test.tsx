import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $update, $updateEnded, $updateError, $updateOpen, $updating, applyUpdate, startUpdates } from '@/store/updates'

import { subjectsIn, UpdateBar, UpdateStrip } from './update-bar'

const AVAILABLE: UpdateStatus = {
  available: true,
  behind: 32,
  commits: Array.from({ length: 30 }, (_, index) => `Change ${String(index + 3)}`),
  commits_pt_br: Array.from({ length: 30 }, () => ''),
  current: 'a',
  dirty: false,
  error: '',
  reason: '',
  supported: true,
  target: 'b'
}

function bridge(overrides: Partial<UpdatesBridge> = {}): UpdatesBridge {
  return {
    apply: vi.fn(() => Promise.resolve({ error: '', ok: true })),
    check: vi.fn(() => Promise.resolve(null)),
    ended: vi.fn(() => Promise.resolve(null)),
    onChange: vi.fn(() => () => undefined),
    openLog: vi.fn(() => Promise.resolve('')),
    status: vi.fn(() => Promise.resolve(null)),
    ...overrides
  }
}

afterEach(() => {
  cleanup()
  $update.set(null)
  $updateEnded.set(null)
  $updateError.set(null)
  $updateOpen.set(false)
  $updating.set(false)
})

describe("the app's update", () => {
  it('shows nothing until a look finds commits main has and this installation lacks', () => {
    render(<UpdateBar />)
    expect(screen.queryByTestId('update-button')).toBeNull()

    act(() => {
      $update.set({ ...AVAILABLE, available: false, behind: 0, commits: [] })
    })
    expect(screen.queryByTestId('update-button')).toBeNull()

    act(() => {
      $update.set(AVAILABLE)
    })
    expect(screen.getByTestId('update-count').textContent).toBe('32')
  })

  it('follows the looks of the main process, and says once how the last update ended', async () => {
    let told: (status: UpdateStatus) => void = () => undefined
    const ended: HandedOff = {
      after: 'b',
      before: 'a',
      commits: ['b1 One', 'b2 Two'],
      ended_at: '',
      error: '',
      log: '',
      ok: true,
      reopen: ''
    }
    startUpdates(
      bridge({
        ended: () => Promise.resolve(ended),
        onChange: listener => {
          told = listener

          return () => undefined
        }
      })
    )
    render(<UpdateBar />)
    await act(async () => {
      await Promise.resolve()
    })
    expect(screen.getByTestId('update-ended').textContent).toContain('Atlas was updated: 2 changes.')

    act(() => {
      told(AVAILABLE)
    })
    expect(screen.getByTestId('update-button')).toBeTruthy()
  })

  it('opens what the update brings and what closing stops, and hands it off', async () => {
    const apply = vi.fn(() => Promise.resolve({ error: '', ok: true }))
    $update.set(AVAILABLE)
    render(<UpdateBar />)

    fireEvent.click(screen.getByTestId('update-button'))
    const dialog = screen.getByTestId('update-dialog')
    expect(dialog.textContent).toContain('32 changes on main that this Atlas lacks:')
    expect(screen.getByTestId('update-commits').querySelectorAll('li')).toHaveLength(31)
    expect(dialog.textContent).toContain('and 2 more')
    expect(dialog.textContent).toContain('opens again by itself')

    await act(async () => {
      await applyUpdate(bridge({ apply }))
    })
    expect(apply).toHaveBeenCalledOnce()
    expect(screen.getByTestId('update-apply').textContent).toBe('Closing to update…')
    expect(screen.getByTestId('update-apply').hasAttribute('disabled')).toBe(true)
  })

  it('stays open and says why when the hand-off does not start', async () => {
    $update.set(AVAILABLE)
    $updateOpen.set(true)
    render(<UpdateBar />)

    await act(async () => {
      await applyUpdate(bridge({ apply: () => Promise.resolve({ error: 'The update did not start.', ok: false }) }))
    })

    expect(screen.getByRole('alert').textContent).toBe('The update did not start.')
    expect(screen.getByTestId('update-apply').textContent).toBe('Update and reopen')
  })

  it('refuses an installation with changes of its own, as the update itself does', () => {
    $update.set({ ...AVAILABLE, dirty: true })
    $updateOpen.set(true)
    render(<UpdateBar />)

    expect(screen.getByTestId('update-dialog').textContent).toContain('changes of its own')
    expect(screen.getByTestId('update-apply').hasAttribute('disabled')).toBe(true)
  })

  it('says a failed update stayed as it was, with its log', () => {
    const openLog = vi.fn(() => Promise.resolve(''))
    window.atlas = { updates: bridge({ openLog }) } as unknown as Window['atlas']
    $updateEnded.set({
      after: '',
      before: '',
      commits: [],
      ended_at: '',
      error: '`npm ci` failed (exit 1)',
      log: 'update.log',
      ok: false,
      reopen: ''
    })
    render(<UpdateBar />)

    expect(screen.getByRole('alert').textContent).toContain(
      'The update failed, and Atlas stayed as it was: `npm ci` failed (exit 1)'
    )
    fireEvent.click(screen.getByText('See what it did'))
    expect(openLog).toHaveBeenCalledOnce()
  })

  it("shows on the hidden sidebar's strip too", () => {
    $update.set(AVAILABLE)
    render(<UpdateStrip />)

    fireEvent.click(screen.getByTestId('update-strip'))
    expect(screen.getByTestId('update-dialog')).toBeTruthy()
  })
})

describe('the commits the update names', () => {
  it("are said in Portuguese from each commit's Notes-pt-BR line, and in English where it has none", () => {
    const update = { ...AVAILABLE, commits: ['The sidebar', 'A fix'], commits_pt_br: ['A barra lateral', ''] }

    expect(subjectsIn(update, 'pt-BR')).toEqual(['A barra lateral', 'A fix'])
    expect(subjectsIn(update, 'en')).toEqual(['The sidebar', 'A fix'])
    expect(subjectsIn({ ...update, commits_pt_br: [] }, 'pt-BR')).toEqual(['The sidebar', 'A fix'])
  })
})
