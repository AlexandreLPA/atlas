import { expect, test } from '@playwright/test'

import { atlas, type Desktop, launch } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

test('a routine runs now from its screen, notifies, and its run opens as a conversation', async () => {
  const { home, model, page } = desktop
  atlas(home, 'routines', 'create', 'headlines', '--schedule', 'weekdays at 9am', '--prompt', 'Three headlines.')
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
  await model.reply({ text: 'One. Two. Three.' })

  await page.getByTestId('routines-entry').click()
  const row = page.getByTestId('routine-row')
  await expect(row).toContainText('headlines')
  await expect(row).toContainText('weekdays at 9am')
  await row.getByRole('button', { name: 'Run now' }).click()

  await expect
    .poll(() => page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications), {
      timeout: 30_000
    })
    .toEqual([['Routine headlines finished', 'One. Two. Three.']])
  await expect(row).toContainText('last answered')
  await row.getByRole('button', { name: 'Runs' }).click()
  await page.getByTestId('routine-runs').getByRole('button', { name: 'Open' }).click()
  await expect(page.getByTestId('assistant-message')).toContainText('One. Two. Three.')
  await expect(page.getByTestId('conversation-row').filter({ hasText: 'headlines' })).toBeVisible()

  await page.getByTestId('routines-entry').click()
  await row.getByRole('button', { name: 'Pause' }).click()
  await expect(row).toContainText('paused')
  await row.getByRole('button', { name: 'Remove' }).click()
  await row.getByRole('button', { name: 'Remove it?' }).click()
  await expect(page.getByText('No routines yet.')).toBeVisible()
})
