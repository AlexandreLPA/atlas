import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
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

const PORTO = 'Lives in Porto; works remotely.'
const LISBON = 'Lives in Lisbon; works remotely.'

const profile = (home: string) => path.join(home, 'memory', 'USER.md')

/** Ten turns, the tenth followed by a review that would rewrite the profile's entry: a proposal. */
async function reviewProposes({ home, model, page }: Desktop): Promise<void> {
  mkdirSync(path.join(home, 'memory'), { recursive: true })
  writeFileSync(profile(home), PORTO)
  const moved = { action: 'replace', content: LISBON, old_text: 'Porto', target: 'user' }
  const review = [{ calls: [{ arguments: JSON.stringify(moved), name: 'memory' }] }, { text: 'Proposed.' }]

  for (let turn = 1; turn <= 10; turn += 1) {
    await model.reply({ text: `Noted ${turn}.` }, ...(turn === 10 ? review : []))
    await say(page, turn === 1 ? 'I moved to Lisbon.' : `Message ${turn}`)
    await expect(page.getByTestId('assistant-message').last()).toHaveText(`Noted ${turn}.`)
  }

  await expect(page.getByTestId('review-summary')).toContainText('User profile change proposed', {
    timeout: 30_000
  })
}

test("the review's proposal is a card in its conversation, and approving it applies it", async () => {
  const { home, page } = desktop
  await reviewProposes(desktop)

  const card = page.getByTestId('proposal-card')
  await expect(card).toContainText(PORTO)
  await expect(card).toContainText(LISBON)
  await expect(page.getByTestId('proposals-entry')).toContainText('1')
  expect(readFileSync(profile(home), 'utf8')).toBe(PORTO) // nothing changes before the approval

  await card.getByRole('button', { name: 'Approve' }).click()

  await expect(page.getByTestId('proposal-applied')).toContainText(`Overwrote, whole:${PORTO}`)
  expect(readFileSync(profile(home), 'utf8')).toBe(LISBON)
  await expect(page.getByTestId('proposals-entry')).toHaveCount(0)
  await page.getByRole('button', { name: 'OK' }).click()
  await expect(page.getByTestId('proposal-card')).toHaveCount(0)
})

test('the list marks a proposal whose entry changed since, and offers only to discard it', async () => {
  const { home, page } = desktop
  await reviewProposes(desktop)
  // The entry changes after the proposal: a conversation rewrote it, or the user by hand.
  writeFileSync(profile(home), 'Lives in Lisbon now.')

  await page.getByTestId('review-summary').getByRole('button', { name: 'Review' }).click()

  const card = page.getByTestId('proposals-view').getByTestId('proposal-card')
  await expect(card.getByTestId('proposal-problem')).toContainText('changed since this was proposed')
  await expect(card.getByRole('button', { name: 'Approve' })).toHaveCount(0)
  await card.getByRole('button', { name: 'Discard' }).click()

  await expect(page.getByTestId('proposals-view')).toContainText('No memory proposals.')
  await expect(page.getByTestId('proposals-entry')).toHaveCount(0)
  expect(readFileSync(profile(home), 'utf8')).toBe('Lives in Lisbon now.')
})
