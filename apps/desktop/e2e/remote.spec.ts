/**
 * The app on a server it did not start, as it reaches Atlas in a container or on another machine:
 * `ATLAS_DESKTOP_URL` and `ATLAS_DESKTOP_TOKEN` (docs/ARCHITECTURE.md, Desktop app and Container).
 * The image's own tests prove the container; this proves the app's half on both systems.
 */
import { expect, test } from '@playwright/test'

import { editHome, homeButton, launchRemote, openHomeMenu, type RemoteDesktop, say } from './harness'

let desktop: RemoteDesktop

test.beforeEach(async () => {
  desktop = await launchRemote()
})

test.afterEach(async () => {
  await desktop.close()
})

test('the app talks to a server it did not start, and comes back when that server restarts', async () => {
  const { app, model, page } = desktop
  // It started nothing of its own.
  expect(await app.evaluate(() => (globalThis as unknown as { atlasServer: unknown }).atlasServer)).toBeNull()
  // Its homes are another machine's: made and imported in that machine's terminal; a backup comes
  // down the connection (step 15, proved in machines.spec.ts).
  await expect(homeButton(page)).toHaveAttribute('data-home', 'default')
  const menu = await openHomeMenu(page)
  await expect(menu.getByTestId('home-new')).toHaveAttribute('data-disabled', '')
  await page.keyboard.press('Escape')
  const profile = await editHome(page)
  await expect(profile.getByTestId('home-import')).toBeDisabled()
  await expect(profile.getByTestId('home-backup')).toBeEnabled()
  await page.keyboard.press('Escape')
  await expect(profile).toBeHidden()

  await model.reply({ text: 'Answered by the server you started.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toHaveText('Answered by the server you started.')

  // As `docker compose restart` does: gone, then back on the same port with the same token.
  desktop.server.stop()
  await expect(page.locator('[data-connection="open"]')).toBeHidden({ timeout: 30_000 })
  await desktop.startServer()
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 60_000 })

  await model.reply({ text: 'Still here.' })
  await say(page, 'Are you back?')
  await expect(page.getByTestId('assistant-message')).toHaveText(['Answered by the server you started.', 'Still here.'])
})
