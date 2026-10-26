import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { _electron, expect, test } from '@playwright/test'

import { cleanEnvironment, DESKTOP, type Desktop, launch, manual, say } from './harness'

/**
 * Opening the app and moving between conversations (step 9d, the UI review of 25/10/2026): the
 * window never says a home with conversations has none while its server starts, a new
 * conversation is listed as soon as its first turn starts, and each conversation keeps the words
 * typed in its box.
 */

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

test('reopened, the window says Atlas opens and lists the conversations, never that there are none', async () => {
  const { home, model, page } = desktop
  await model.reply({ text: 'Noted.' })
  await say(page, 'A conversation to find again')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Noted.')
  await desktop.app.close()

  const root = path.dirname(home)
  const app = await _electron.launch({
    args: ['.', '--atlas-e2e', '--lang=en-US', `--user-data-dir=${path.join(root, 'profile')}`],
    cwd: DESKTOP,
    env: cleanEnvironment({ ATLAS_HOME: home })
  })

  try {
    const page = await app.firstWindow()
    // Watched from the window's first moments: what the screen ever said while the server started.
    await page.evaluate(() => {
      const seen = { empty: false, opening: false }
      Object.assign(window, { seenWhileOpening: seen })
      new MutationObserver(() => {
        const words = document.body.innerText
        // The invitation to start is right once the home answered, never before.
        const open = document.querySelector('[data-connection="open"]') !== null
        seen.empty ||= words.includes('No conversations yet.') || (!open && words.includes('Ask Atlas anything.'))
        seen.opening ||= words.includes('Opening Atlas…')
      }).observe(document.documentElement, { characterData: true, childList: true, subtree: true })
    })

    await expect(page.getByTestId('conversation-row')).toHaveText(/A conversation to find again/, { timeout: 90_000 })
    const seen = await page.evaluate(
      () => (window as unknown as { seenWhileOpening: { empty: boolean; opening: boolean } }).seenWhileOpening
    )
    expect(seen.empty).toBe(false)
    expect(seen.opening).toBe(true)
  } finally {
    await app.close().catch(() => undefined)
  }
})

test('a new conversation is listed while its first turn waits for an approval', async () => {
  const { folder, model, page } = desktop
  mkdirSync(path.join(folder, 'build'))
  writeFileSync(path.join(folder, 'build', 'out.txt'), 'compiled')
  await model.reply(
    { calls: [{ arguments: JSON.stringify({ command: 'rm -r build' }), name: 'run_command' }] },
    { text: 'ESCALATE' },
    { text: 'Removed.' }
  )
  await manual(page)
  await say(page, 'Delete the build folder')

  await expect(page.getByTestId('approval-card')).toBeVisible()
  // Listed from its start, with the dot of what it waits for (it was left out until the turn ended).
  const row = page.getByTestId('conversation-row')
  await expect(row).toHaveCount(1)
  await expect(row).toContainText('Delete the build folder')
  await expect(row.getByTestId('status-dot')).toHaveAttribute('data-dot', 'waiting')
  await page.getByTestId('approval-card').getByRole('button', { name: 'Deny' }).click()
})

test("each conversation keeps the words typed in its box, and a new one's wait for it", async () => {
  const { model, page } = desktop
  await model.reply({ text: 'First answer.' })
  await say(page, 'The first conversation')
  await expect(page.getByTestId('assistant-message').last()).toContainText('First answer.')

  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  await box.fill('Words for the first one')
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(box).toHaveValue('')
  await box.fill('Words for a new one')

  await page.getByTestId('conversation-row').first().click()
  await expect(box).toHaveValue('Words for the first one')
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(box).toHaveValue('Words for a new one')
})
