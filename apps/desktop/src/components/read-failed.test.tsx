import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import * as app from '@/store/app'

import { ProposalsView } from './proposals-view'
import { RoutinesView } from './routines-view'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$readError.set({})
})

describe('a page whose read failed (the UI review, 25/10/2026)', () => {
  it('says why in its place, never that it is empty, and reads again', () => {
    const again = vi.spyOn(app, 'readAgain').mockImplementation(() => undefined)
    app.$readError.setKey('proposals', 'the server is gone')
    render(<ProposalsView />)

    expect(screen.getByTestId('read-failed').textContent).toContain(t.lists.failed('the server is gone'))
    expect(screen.queryByText(t.proposals.empty)).toBeNull()
    fireEvent.click(screen.getByTestId('read-again'))
    expect(again).toHaveBeenCalledWith('proposals')
  })

  it('is empty only once a read worked', () => {
    render(<RoutinesView />)
    expect(screen.queryByTestId('read-failed')).toBeNull()
    expect(screen.getByText(t.routines.empty)).toBeTruthy()
  })
})
