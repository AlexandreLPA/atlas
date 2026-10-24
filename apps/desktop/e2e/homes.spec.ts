/**
 * Several homes on one machine (docs/ARCHITECTURE.md, Backup and homes): the app keeps a server per
 * open home, switching to one starts its server, and a turn in a home off screen goes on and
 * notifies when it ends.
 */
import { expect, type Page, test } from '@playwright/test'

import { type Desktop, homeButton, launch, listedHomes, say, switchHome } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch({ homes: ['work'] })
})

test.afterEach(async () => {
  await desktop.close()
})

const LONG = Array.from({ length: 40 }, (_, n) => `Line ${String(n + 1)} of the report.`).join('\n')

async function watchNotifications(page: Page): Promise<void> {
  await page.evaluate(() => {
    const seen: string[][] = []
    Object.assign(window, { seenNotifications: seen })
    window.Notification = class {
      onclick: (() => void) | null = null

      constructor(title: string, options?: NotificationOptions) {
        seen.push([title, options?.body ?? ''])
      }
    } as unknown as typeof Notification
  })
}

const homesServed = (desktop: Desktop) =>
  desktop.app.evaluate(() => [
    ...(globalThis as unknown as { atlasServers: Map<null | string, unknown> }).atlasServers.keys()
  ])

test('switching homes starts that home, and a turn off screen goes on and notifies', async () => {
  const { model, page } = desktop
  await expect(homeButton(page)).toHaveAttribute('data-home', 'default')
  expect(await listedHomes(page)).toEqual(['default', 'work'])
  expect(await homesServed(desktop)).toEqual([null])

  await model.reply({ text: 'Answered at home.' })
  await say(page, 'Hello from default')
  await expect(page.getByTestId('assistant-message')).toHaveText('Answered at home.')

  // Another home: its own server, its own conversations.
  await switchHome(page, 'work')
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })
  await expect(page.getByTestId('conversation-row')).toHaveCount(0)
  expect(await homesServed(desktop)).toEqual([null, 'work'])

  // A long turn there, then back to the first home while it runs.
  await watchNotifications(page)
  await model.reply({ pause: 0.03, text: LONG })
  await say(page, 'Write the report')
  await expect(page.getByTestId('assistant-message')).toContainText('Line 1')
  await switchHome(page, 'default')
  await expect(page.getByTestId('conversation-title')).toHaveText(['Hello from default'])
  await expect(page.getByTestId('assistant-message')).toHaveText('Answered at home.')

  await expect
    .poll(() => page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications), {
      timeout: 30_000
    })
    .toEqual([['Atlas answered', 'Write the report (home work)']])

  // Back in the other home, the whole reply is there.
  await switchHome(page, 'work')
  await expect(page.getByTestId('assistant-message')).toContainText('Line 40 of the report.')
})
