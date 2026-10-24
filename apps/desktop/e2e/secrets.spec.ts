import { expect, test } from '@playwright/test'

import { atlas, type Desktop, launch, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

// The secrets tool is deferred, so the model finds it with tool search first (step 16d).
const search = { calls: [{ arguments: JSON.stringify({ query: 'secrets' }), name: 'tool_search' }] }
const request = (name: string, reason: string) => ({
  calls: [{ arguments: JSON.stringify({ action: 'request', name, reason }), name: 'secrets' }]
})

test('the secrets card: the pasted key goes to the vault, and the model hears only stored', async () => {
  const { home, model, page } = desktop
  const pasted = `sk-live-E2E-${'D'.repeat(40)}`
  await model.reply(search, request('STRIPE_API_KEY', 'to list my charges'), { text: 'Stored, thanks.' })
  await say(page, 'Set up Stripe')

  const card = page.getByTestId('secret-card')
  await expect(card).toContainText('STRIPE_API_KEY')
  await expect(card).toContainText('to list my charges')
  const field = card.getByTestId('secret-input')
  await expect(field).toHaveAttribute('type', 'password')
  await card.getByTestId('secret-eye').click()
  await expect(field).toHaveAttribute('type', 'text')
  await card.getByTestId('secret-eye').click()

  await field.fill(pasted)
  await card.getByTestId('secret-save').click()

  await expect(card).toBeHidden()
  await expect(page.getByTestId('assistant-message')).toContainText('Stored, thanks.')
  // The name is in the vault's list; the value is in no page and no request the model got.
  expect(atlas(home, 'secrets', 'list')).toContain('STRIPE_API_KEY')
  expect(await page.content()).not.toContain(pasted)
  expect(await model.seen()).not.toContain(pasted)
  const last = (await model.requests()).at(-1)
  expect(JSON.stringify(last?.body?.messages?.at(-1))).toContain('stored')
})

test('the skip button: the card closes, and the model hears that the user skipped', async () => {
  const { home, model, page } = desktop
  await model.reply(search, request('STRIPE_API_KEY', 'to list my charges'), { text: 'Without it, then.' })
  await say(page, 'Set up Stripe')

  const card = page.getByTestId('secret-card')
  await expect(card).toContainText('STRIPE_API_KEY')
  await card.getByTestId('secret-skip').click()

  await expect(card).toBeHidden()
  await expect(page.getByTestId('assistant-message')).toContainText('Without it, then.')
  expect(atlas(home, 'secrets', 'list')).not.toContain('STRIPE_API_KEY')
  const last = (await model.requests()).at(-1)
  expect(JSON.stringify(last?.body?.messages?.at(-1))).toContain('skipped')
})
