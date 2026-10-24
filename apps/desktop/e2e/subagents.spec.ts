import { writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, ROOT, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch({ lang: 'en' })
})

test.afterEach(async () => {
  await desktop.close()
})

/** Only the parent's system prompt holds the memory guidance; each child's holds its context. */
const PARENT = 'Skills come first'

test('a subagent shows on the agent map, takes a correction, stops, and its batch comes back as a notice', async () => {
  const { model, page } = desktop
  const tasks = [{ context: 'ctx-photos', goal: 'Index the photo folder by year.' }]
  await model.reply(
    { calls: [{ arguments: JSON.stringify({ tasks }), name: 'delegate' }], when: PARENT },
    { text: 'A helper is indexing your photos.', when: PARENT },
    { text: 'never sent', wait_before: 30, when: 'ctx-photos' },
    { text: 'The indexing was stopped before it finished.', when: PARENT }
  )
  await say(page, 'Index my photos')

  const button = page.getByTestId('agents-button')
  await expect(button).toContainText('1 agent')
  await button.click()

  const map = page.getByTestId('agent-map')
  const row = map.getByTestId('subagent-row')
  await expect(row).toContainText('Index the photo folder by year.')
  await expect(map).toContainText('1 subagent working')

  await row.getByRole('button', { name: /Index the photo folder/ }).click()
  await row.getByRole('button', { name: 'Steer' }).click()
  await row.getByRole('textbox').fill('Skip the videos.')
  await row.getByRole('button', { name: 'Send' }).click()
  await expect(row.getByRole('textbox')).toBeHidden()

  await row.getByRole('button', { name: 'Stop' }).click()

  await expect(map).toBeHidden()
  await expect(button).toBeHidden()
  const notice = page.getByTestId('notice')
  await expect(notice).toHaveCount(1)
  await notice.click()
  await expect(notice).toContainText('status=interrupted')
  await expect(page.getByTestId('assistant-message').last()).toContainText('The indexing was stopped')
})

/** A command that prints and then waits a minute, in the shell run_command uses; a script file,
 * since `python -c` asks for approval. */
function sleeper(folder: string): string {
  const script = path.join(folder, 'sleeper.py')
  writeFileSync(script, 'import time\nprint("warming up", flush=True)\ntime.sleep(60)\n')
  const python =
    process.platform === 'win32'
      ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
      : path.join(ROOT, '.venv', 'bin', 'python')
  const words = `'${python}' '${script}'`

  return process.platform === 'win32' ? `& ${words}` : words
}

test('a background command shows on the agent map with its output, and stops from it', async () => {
  const { folder, model, page } = desktop
  const start = { background: true, command: sleeper(folder), description: 'Build the game' }
  await model.reply(
    { calls: [{ arguments: JSON.stringify(start), name: 'run_command' }] },
    { text: 'The build runs in the background.' }
  )
  await say(page, 'Build the game')

  const button = page.getByTestId('agents-button')
  await expect(button).toContainText('1 task')
  await button.click()

  const map = page.getByTestId('agent-map')
  await expect(map).toContainText('1 background task')
  const row = map.getByTestId('task-row')
  await expect(row).toContainText('Build the game')
  await expect(row).toContainText('cmd-1')

  await row.getByRole('button', { name: /Build the game/ }).click()
  await expect(row.getByTestId('task-output')).toContainText('warming up')

  const stop = row.getByRole('button', { exact: true, name: 'Stop' })
  await stop.click()
  await expect(row).toContainText('Stopped')
  await expect(row.getByTestId('task-output')).toContainText('it was stopped')
  await expect(stop).toBeHidden()
  await expect(button).toBeHidden()

  await map.getByRole('button', { name: 'Close' }).click()
  await expect(map).toBeHidden()
})
