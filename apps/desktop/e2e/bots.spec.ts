import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { atlas, type Desktop, launch, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

test('a bot is made on the screen, answers as itself in its own conversation, and is edited and deleted', async () => {
  const { home, model, page } = desktop

  // Made from three fields: the name becomes its handle, and it opens.
  await page.getByTestId('tab-bots').click()
  await expect(page.getByText('No bots yet.')).toBeVisible()
  await page.getByTestId('new-bot').click()
  await page.getByTestId('bot-name').fill('Finance')
  await page.getByTestId('bot-title').fill('Finance')
  await page.getByTestId('bot-description').fill('Bills and taxes.')
  await page.getByTestId('bot-save').click()

  await expect(page.getByTestId('bot-dialog')).toBeHidden()
  const row = page.getByTestId('bot-row')
  await expect(row).toContainText('Finance')
  await expect(row).toContainText('@finance')
  await expect(page.getByTestId('bot-header')).toContainText('Bills and taxes.')
  await expect(page.getByTestId('bot-empty')).toContainText('Finance')
  const definition = path.join(home, 'bots', 'finance', 'BOT.md')
  expect(readFileSync(definition, 'utf8')).toContain('**Mission:** Bills and taxes.')

  // It answers as itself, with its own mission in the place of Atlas's identity.
  await model.reply({ text: 'The rent was paid on the 5th.' })
  await say(page, 'Was the rent paid?', 'Finance')
  await expect(page.getByTestId('assistant-message')).toHaveText('The rent was paid on the 5th.')
  const [first] = await model.requests()
  expect(JSON.stringify(first?.body)).toContain('**Mission:** Bills and taxes.')
  await expect(row).toContainText('The rent was paid on the 5th.')

  // Its conversation is its own: not among the conversations, and the same one on every click.
  await page.getByTestId('tab-conversations').click()
  await expect(page.getByTestId('conversation-row')).toHaveCount(0)
  await page.getByTestId('tab-bots').click()
  await row.click()
  await expect(page.getByTestId('user-message')).toHaveText('Was the rent paid?')

  // Its routines are one click from its conversation.
  atlas(home, 'routines', 'create', 'bills', '--schedule', 'weekdays at 9am', '--prompt', 'Bills.', '--bot', 'finance')
  await page.getByTestId('routines-entry').click()
  await expect(page.getByTestId('routine-row')).toContainText('@finance')
  await row.click()
  await expect(page.getByTestId('bot-routines')).toHaveText('1 routine')

  // Edited: its mission in the user's words reaches its next turn.
  await page.getByTestId('bot-edit').click()
  await expect(page.getByTestId('bot-name')).toBeDisabled()
  await page.getByTestId('bot-title').fill('Money')
  await page.getByTestId('bot-advanced').click()
  await page.getByTestId('bot-mission').fill('Never pay late fees.')
  await page.getByTestId('bot-save').click()
  await expect(page.getByTestId('bot-header')).toContainText('Money')
  await model.reply({ text: 'No fees this month.' })
  await say(page, 'Any fees?', 'Money')
  await expect(page.getByTestId('assistant-message').last()).toHaveText('No fees this month.')
  const requests = await model.requests()
  expect(JSON.stringify(requests.at(-1)?.body)).toContain('Never pay late fees.')

  // Deleted, asked twice: it leaves the list with its folder and its routine.
  await page.getByTestId('bot-edit').click()
  await page.getByTestId('bot-delete').click()
  await page.getByTestId('bot-delete-confirm').click()
  await expect(page.getByText('No bots yet.')).toBeVisible()
  expect(existsSync(path.join(home, 'bots', 'finance'))).toBe(false)
  await page.getByTestId('routines-entry').click()
  await expect(page.getByText('No routines yet.')).toBeVisible()
})
