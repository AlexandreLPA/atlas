import { writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

const LONG = Array.from({ length: 40 }, (_, n) => `Line ${n + 1} of a long answer.`).join(' ')

test('stop: the turn ends, and a message that never entered comes back to the box', async () => {
  const { model, page } = desktop
  await model.reply({ pause: 0.2, text: LONG })
  await say(page, 'Tell me a long story')
  await expect(page.getByTestId('assistant-message')).toContainText('Line 1')

  // No tool round in this turn, so the steering message waits, and the stop hands it back.
  await say(page, 'and make it rhyme')
  await page.getByRole('button', { name: 'Stop' }).click()

  await expect(page.getByTestId('turn-end')).toHaveText('Stopped.')
  await expect(page.getByRole('textbox', { name: 'Message Atlas' })).toHaveValue('and make it rhyme')
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden()

  await model.reply({ text: 'Here is a rhyme.' })
  await page.getByRole('textbox', { name: 'Message Atlas' }).press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Here is a rhyme.')
})

test('a message sent mid-turn enters after the tools in course', async () => {
  const { folder, model, page } = desktop
  writeFileSync(path.join(folder, 'notes.txt'), 'buy milk\n')
  await model.reply(
    {
      calls: [{ arguments: JSON.stringify({ path: 'notes.txt' }), name: 'read_file' }],
      pause: 0.1,
      text: 'Let me look at your notes first, one moment.'
    },
    { text: 'Your notes say: buy milk.' }
  )
  await say(page, 'Summarize my notes')
  await expect(page.getByTestId('assistant-message')).toContainText('Let me')
  await say(page, 'answer in one line')

  await expect(page.getByText('sent during the turn')).toBeVisible()
  await expect(page.getByTestId('assistant-message').last()).toContainText('Your notes say: buy milk.')
  const second = (await model.requests())[1]
  expect(JSON.stringify(second?.body?.messages)).toContain('answer in one line')
})

test('a dropped connection loses nothing: the reply is whole, and painted once', async () => {
  const { model, page } = desktop
  await model.reply({ pause: 0.02, text: LONG })
  await say(page, 'Tell me a long story')
  await expect(page.getByTestId('assistant-message')).toContainText('Line 2')

  await page.evaluate(() => {
    window.atlasE2e?.reconnect()
  })

  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: 30_000 })
  await expect(page.getByTestId('assistant-message')).toHaveCount(1)
  await expect(page.getByTestId('assistant-message')).toHaveText(LONG)
  await expect(page.getByTestId('user-message')).toHaveCount(1)
})

test('a turn that ends in a conversation not on screen sends a system notification', async () => {
  const { model, page } = desktop
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
  await model.reply({ pause: 0.02, text: LONG })
  await say(page, 'Tell me a long story')
  await expect(page.getByTestId('assistant-message')).toContainText('Line 1')
  await page.getByRole('button', { name: 'New conversation' }).click()

  await expect
    .poll(() => page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications), {
      timeout: 30_000
    })
    .toEqual([['Atlas answered', 'Tell me a long story']])
})
