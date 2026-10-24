import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { HarnessAutoWarning, useHarnessAuto } from './harness-auto'

afterEach(() => {
  cleanup()
})

/** The ⚡ of a session as the box draws it, with its warning where the box puts its note. */
function Lightning({ on, set }: { on: boolean; set: (on: boolean) => void }) {
  const auto = useHarnessAuto(set)

  return (
    <>
      <button
        aria-pressed={on}
        onClick={() => {
          auto.toggle(!on)
        }}
        type="button"
      >
        ⚡
      </button>
      {auto.asking && <HarnessAutoWarning onCancel={auto.cancel} onConfirm={auto.confirm} title="Claude Code" />}
    </>
  )
}

describe("an executor session's automatic mode (17/10/2026)", () => {
  it('turns on only after the warning is read and confirmed', () => {
    const set = vi.fn()
    render(<Lightning on={false} set={set} />)

    fireEvent.click(screen.getByText('⚡'))

    expect(set).not.toHaveBeenCalled()
    const warning = screen.getByTestId('harness-auto-warning')
    expect(warning.textContent).toContain('approve its own permission requests in this session')
    expect(warning.textContent).toContain('it may run commands and edit files')
    expect(warning.textContent).toContain('your Claude Code settings stay as they are')
    expect(warning.textContent).toContain('it may commit and push')
    expect(warning.textContent).toContain('add an ask rule to your Claude Code settings')

    fireEvent.click(screen.getByTestId('harness-auto-confirm'))

    expect(set).toHaveBeenCalledExactlyOnceWith(true)
    expect(screen.queryByTestId('harness-auto-warning')).toBeNull()
  })

  it('stays off when the user keeps asking', () => {
    const set = vi.fn()
    render(<Lightning on={false} set={set} />)

    fireEvent.click(screen.getByText('⚡'))
    fireEvent.click(screen.getByTestId('harness-auto-cancel'))

    expect(set).not.toHaveBeenCalled()
    expect(screen.queryByTestId('harness-auto-warning')).toBeNull()
  })

  it('turns off at once, with no warning', () => {
    const set = vi.fn()
    render(<Lightning on set={set} />)

    fireEvent.click(screen.getByText('⚡'))

    expect(set).toHaveBeenCalledExactlyOnceWith(false)
    expect(screen.queryByTestId('harness-auto-warning')).toBeNull()
  })
})
