import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { type Desktop, launch, manual, say } from './harness'

/**
 * The turn on screen (step 9c), with the real app, the real `atlas serve` and the fake model: a
 * turn with reasoning, interim text, a call that fails and one the user denies, drawn as a trail;
 * and the same conversation reopened draws the same trail, but for the times.
 */

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

/** Each part on the trail: its kind, its dot, and what it says, the times left out. */
async function trail(page: Page): Promise<string[]> {
  return page.getByTestId('part').evaluateAll(parts =>
    parts.map(part => {
      const kind = part.getAttribute('data-kind') ?? ''
      const dot = part.getAttribute('data-dot') ?? ''
      let said = ''

      if (kind === 'text') {
        said = part.textContent.trim()
      } else if (kind === 'tool') {
        const summary = part.querySelector('summary')
        const state = summary?.querySelector('[data-testid="tool-state"]')?.textContent ?? ''
        said = (summary?.textContent ?? '').replace(state, '').trim()
      } else if (kind === 'reasoning') {
        // "Thought for 0.2 s · about" live, "Thought · about" reopened: the subject only.
        said = (part.querySelector('summary')?.textContent ?? '').split(' · ').slice(1).join(' · ')
      }

      return `${kind} ${dot} ${said}`
    })
  )
}

test('a turn draws as a trail of parts, and reopens the same', async () => {
  const { folder, model, page } = desktop
  const build = path.join(folder, 'build')
  mkdirSync(build)
  writeFileSync(path.join(build, 'out.txt'), 'compiled')
  await model.reply(
    {
      calls: [{ arguments: JSON.stringify({ path: 'missing.md' }), name: 'read_file' }],
      reasoning: '**Finding the notes**\n\nThey should be in the working folder.',
      text: 'Let me read the notes.'
    },
    {
      calls: [{ arguments: JSON.stringify({ command: 'rm -r build' }), name: 'run_command' }],
      text: 'There are none. I will clear the build folder instead.'
    },
    { text: 'ESCALATE' },
    { text: 'I left the build folder, as you asked.' }
  )
  await manual(page)
  await say(page, 'Read my notes')

  const card = page.getByTestId('approval-card')
  await card.getByRole('button', { name: 'Deny' }).click()
  await expect(page.getByTestId('assistant-message').last()).toHaveText('I left the build folder, as you asked.')
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden()

  const live = await trail(page)
  expect(live).toEqual([
    'reasoning reasoning Finding the notes',
    'text interim Let me read the notes.',
    'tool failed Read missing.md',
    'text interim There are none. I will clear the build folder instead.',
    'tool denied Ran rm -r build',
    'text final I left the build folder, as you asked.'
  ])
  expect(existsSync(build)).toBe(true)
  await expect(page.getByTestId('assistant-message').first()).toHaveAttribute('data-role', 'interim')

  // IN and OUT: what went in, and what came out, red for the failed call.
  const failed = page.getByTestId('tool-row').first()
  await failed.locator('summary').click()
  await expect(failed).toContainText('IN')
  await expect(failed).toContainText('missing.md')
  await expect(failed).toContainText('OUT')
  await page.screenshot({ path: test.info().outputPath('trail-live.png') })

  // Reopened: another conversation, then this one again, read from the server.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await page.getByTestId('conversation-row').first().click()
  await expect(page.getByTestId('part')).toHaveCount(live.length)
  expect(await trail(page)).toEqual(live)
  await expect(page.getByTestId('reasoning').locator('summary')).toHaveText('Thought · Finding the notes')
  await page.screenshot({ path: test.info().outputPath('trail-reopened.png') })
})
