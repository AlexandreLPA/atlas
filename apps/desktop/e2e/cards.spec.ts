import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, manual, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

const ask = (questions: unknown) => ({ calls: [{ arguments: JSON.stringify({ questions }), name: 'ask_user' }] })
const remove = { calls: [{ arguments: JSON.stringify({ command: 'rm -r build' }), name: 'run_command' }] }

function build(folder: string): string {
  const target = path.join(folder, 'build')
  mkdirSync(target)
  writeFileSync(path.join(target, 'out.txt'), 'compiled')

  return target
}

test("ask_user's card: the choice clicked is what the model receives", async () => {
  const { model, page } = desktop
  await model.reply(ask([{ choices: ['Green', 'Blue'], question: 'Which colour?' }]), { text: 'Blue it is.' })
  await say(page, 'Pick a colour with me')

  const card = page.getByTestId('ask-card')
  await expect(card).toContainText('Which colour?')
  await card.getByRole('button', { name: 'Blue' }).click()
  await card.getByRole('button', { name: 'Answer' }).click()

  await expect(card).toBeHidden()
  await expect(page.getByTestId('assistant-message')).toContainText('Blue it is.')
  const second = (await model.requests())[1]
  expect(JSON.stringify(second?.body?.messages?.at(-1))).toContain('Blue')
})

test('approval, once: the command runs only after the click', async () => {
  const { folder, model, page } = desktop
  const target = build(folder)
  // The turn's call, then the guardian's verdict, then the reply after the command ran.
  await model.reply(remove, { text: 'ESCALATE' }, { text: 'Removed the build folder.' })
  await manual(page)
  await say(page, 'Delete the build folder')

  const card = page.getByTestId('approval-card')
  await expect(card).toContainText('rm -r build')
  expect(existsSync(target)).toBe(true)
  await expect(card.getByRole('button')).toHaveText(['Allow once', 'This conversation', 'Always', 'Deny'])
  await card.getByRole('button', { name: 'Allow once' }).click()

  await expect(card).toBeHidden()
  await expect(page.getByTestId('assistant-message')).toContainText('Removed the build folder.')
  expect(existsSync(target)).toBe(false)
})

test('approval, deny with a reason: the command never runs and the model reads why', async () => {
  const { folder, model, page } = desktop
  const target = build(folder)
  await model.reply(remove, { text: 'ESCALATE' }, { text: 'Understood, I left it.' })
  await manual(page)
  await say(page, 'Delete the build folder')

  const card = page.getByTestId('approval-card')
  await card.getByRole('textbox').fill('keep the build, use git clean instead')
  await card.getByRole('button', { name: 'Deny' }).click()

  await expect(page.getByTestId('assistant-message')).toContainText('Understood, I left it.')
  expect(existsSync(target)).toBe(true)
  expect(await model.seen()).toContain('keep the build, use git clean instead')
})

test('"Auto", on from the start, skips the guardian and the card', async () => {
  const { folder, model, page } = desktop
  const target = build(folder)
  const lightning = page.getByRole('button', { name: 'Auto mode' })
  await expect(lightning).toHaveAttribute('aria-pressed', 'true')
  // No guardian call: only the turn's two calls are scripted.
  await model.reply(remove, { text: 'Removed it.' })
  await say(page, 'Delete the build folder')

  await expect(page.getByTestId('assistant-message')).toContainText('Removed it.')
  await expect(page.getByTestId('approval-card')).toHaveCount(0)
  expect(existsSync(target)).toBe(false)
  expect(await model.requests()).toHaveLength(2)
})
