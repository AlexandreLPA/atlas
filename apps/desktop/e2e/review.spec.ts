import { mkdir, writeFile } from 'node:fs/promises'
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

test("the background review's summary reaches the conversation", async () => {
  const { model, page } = desktop

  // Ten user messages with no memory write make the review due right after the tenth turn, so its
  // two calls are queued behind that turn's reply.
  const review = [
    {
      calls: [
        {
          arguments: JSON.stringify({ action: 'add', content: 'Ana; likes short answers.', target: 'user' }),
          name: 'memory'
        }
      ]
    },
    { text: 'Saved.' }
  ]

  for (let turn = 1; turn <= 10; turn += 1) {
    await model.reply({ text: `Noted ${turn}.` }, ...(turn === 10 ? review : []))
    await say(page, turn === 1 ? 'My name is Ana and I like short answers.' : `Message ${turn}`)
    await expect(page.getByTestId('assistant-message').last()).toHaveText(`Noted ${turn}.`)
  }

  await expect(page.getByTestId('review-summary')).toContainText('User profile updated', { timeout: 30_000 })
})

test('a skill the review changed shows its diff in a click', async () => {
  const { home, model, page } = desktop
  const skill = path.join(home, 'skills', 'notes')

  await mkdir(skill, { recursive: true })
  await writeFile(
    path.join(skill, 'SKILL.md'),
    '---\nname: notes\ndescription: Use when writing notes.\n---\nWrite in bullets.\nKeep each one short.\n'
  )

  // The review loads the skill, as it must before changing it, then patches one line.
  const patch = {
    operations: [
      {
        action: 'patch',
        name: 'notes',
        new_string: 'Write in bullets, at most five.',
        old_string: 'Write in bullets.'
      }
    ]
  }

  const review = [
    { calls: [{ arguments: JSON.stringify({ name: 'notes' }), name: 'use_skill' }] },
    { calls: [{ arguments: JSON.stringify(patch), name: 'skill_manage' }] },
    { text: 'Patched.' }
  ]

  for (let turn = 1; turn <= 10; turn += 1) {
    await model.reply({ text: `Noted ${turn}.` }, ...(turn === 10 ? review : []))
    await say(page, turn === 1 ? 'Five bullets at most in my notes.' : `Message ${turn}`)
    await expect(page.getByTestId('assistant-message').last()).toHaveText(`Noted ${turn}.`)
  }

  const action = page.getByRole('button', { name: "Skill 'notes' updated" })

  await expect(action).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('skill-diff')).toHaveCount(0)
  await action.click()

  const diff = page.getByTestId('skill-diff')

  await expect(diff.locator('[data-kind="file"]')).toHaveText('SKILL.md')
  await expect(diff.locator('[data-kind="minus"]')).toHaveText('- Write in bullets.')
  await expect(diff.locator('[data-kind="plus"]')).toHaveText('+ Write in bullets, at most five.')
  await expect(diff.locator('[data-kind="plus"] mark')).toHaveText('bullets, at most five.')
  await expect(diff).not.toContainText('name: notes') // only the lines around the change
  await expect(diff.locator('[data-kind="plus"]')).toBeInViewport({ ratio: 1 }) // scrolled into sight

  await action.click()
  await expect(diff).toHaveCount(0)
})
