/**
 * The app's update, end to end (asked for by the user on 18/10/2026): the real app looks for one
 * as it opens, its button shows what `main` brings, "Update and reopen" hands the update off and
 * closes the app, and the app opened again says how it went. The hand-off is `tests/fake_update.py`
 * standing in for `python -m atlas.installation` (`ATLAS_E2E_UPDATE`): it takes the hand-off as
 * the real one does and waits, for real, for the app to be gone; the update between is proved on
 * real git by `tests/installation/test_installation.py`.
 */
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

import { type ElectronApplication, expect, test } from '@playwright/test'

import { type Desktop, launch, openApp, ROOT } from './harness'

const STAND_IN = { ATLAS_E2E_UPDATE: path.join(ROOT, 'tests', 'fake_update.py') }

let desktop: Desktop | undefined
let again: ElectronApplication | undefined

test.afterEach(async () => {
  await again?.close().catch(() => undefined)
  again = undefined
  await desktop?.close()
  desktop = undefined
})

test('the update shows when main has more, closes the app to update it, and the app says so after', async () => {
  desktop = await launch({ environment: STAND_IN })
  const { app, home, page } = desktop

  // The first look runs as it opens; the button says how many commits main brings.
  await expect(page.getByTestId('update-button')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('update-count')).toHaveText('2')
  await page.getByTestId('update-button').click()
  await expect(page.getByTestId('update-dialog')).toContainText('2 changes on main that this Atlas lacks:')
  await expect(page.getByTestId('update-commits').locator('li')).toHaveText([
    'Step 18: the button',
    'Step 18: the hand-off'
  ])

  const closed = new Promise<void>(resolve => {
    app.once('close', () => {
      resolve()
    })
  })
  await page.getByTestId('update-apply').click()
  await closed

  // The hand-off waited for the app to be gone, then said how it went, in the app's own folder.
  const root = path.dirname(home)
  const result = path.join(root, 'profile', 'update-result.json')
  await expect.poll(() => existsSync(result), { timeout: 30_000 }).toBe(true)
  expect(JSON.parse(readFileSync(result, 'utf8'))).toMatchObject({ ok: true })
  expect(existsSync(path.join(root, 'profile', 'update-running.json'))).toBe(false)

  const reopened = await openApp(root, home, { CLAUDE_CONFIG_DIR: desktop.claude, ...STAND_IN })
  again = reopened.app
  await expect(reopened.page.getByTestId('update-ended')).toContainText('Atlas was updated: 2 changes.')
  await reopened.page.getByTestId('update-ended').getByRole('button').last().click()
  await expect(reopened.page.getByTestId('update-ended')).toBeHidden()
  expect(existsSync(result)).toBe(false)
})
