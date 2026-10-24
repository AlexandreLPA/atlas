import { spawnSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { cleanEnvironment, type Desktop, launch, ROOT, say } from './harness'

// Two models the fake server answers as, declared in the home's models.toml as a user declares
// a local server's models: the picker offers them under vLLM.
const MODELS = `[[models]]
provider = "vllm"
id = "fake-model"
name = "Fake"
tool_call = true
reasoning = true
efforts = ["low", "high"]

[[models]]
provider = "vllm"
id = "fake-other"
name = "Fake Other"
tool_call = true
reasoning = true
efforts = ["low", "high"]
`

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

test('a conversation picks another model and effort, and its next turn runs on them', async () => {
  desktop = await launch({ models: MODELS })
  const { model, page } = desktop
  const picker = page.getByRole('combobox', { name: /^Model/ })
  const effort = page.getByRole('combobox', { name: /^Effort/ })
  await expect(picker).toHaveValue('vllm\u0000fake-model')
  // The default model and Atlas's default effort, medium onto what the model takes, are marked.
  await expect(picker.locator('option')).toHaveText(['Fake · default', 'Fake Other'])
  await expect(effort).toHaveValue('low')
  await expect(effort.locator('option')).toHaveText(['Low · default', 'High'])

  await model.reply({ text: 'On the default.' }, { text: 'On the other.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toContainText('On the default.')

  await picker.selectOption({ label: 'Fake Other' })
  await expect(picker).toHaveValue('vllm\u0000fake-other')
  await effort.selectOption({ label: 'High' })
  await expect(effort).toHaveValue('high')
  await say(page, 'And now?')
  await expect(page.getByTestId('assistant-message').last()).toContainText('On the other.')

  const requests = (await model.requests()).filter(request => request.path.endsWith('/chat/completions'))
  const bodies = requests.map(request => request.body as { model?: string; reasoning_effort?: string })
  expect(bodies.map(body => body.model)).toEqual(['fake-model', 'fake-other'])
  // What the picker showed is what went: the default effort, then the one picked.
  expect(bodies.map(body => body.reasoning_effort)).toEqual(['low', 'high'])
  expect(JSON.stringify(requests[1])).toContain('The active model for this chat has changed to fake-other')
  // The note is for the model: the thread shows only what the user wrote.
  await expect(page.getByTestId('user-message')).toHaveText(['Hello', 'And now?'])

  // A new conversation starts from the default again.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(picker).toHaveValue('vllm\u0000fake-model')
  await page.getByTestId('conversation-row').filter({ hasText: 'Hello' }).click()
  await expect(picker).toHaveValue('vllm\u0000fake-other')
})

test('the window speaks Portuguese on a system in Portuguese', async () => {
  desktop = await launch({ lang: 'pt-BR' })
  const { page } = desktop
  await expect(page.getByPlaceholder('Mensagem para o Atlas')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Enviar' })).toBeVisible()
  await expect(page.getByText('Pergunte qualquer coisa ao Atlas.')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.lang)).toBe('pt-BR')
})

/** The conversations stored in a home, empty ones included: the sidebar hides those. */
function stored(home: string): number {
  const read =
    'import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); ' +
    'print(db.execute("SELECT count(*) FROM conversations_threads").fetchone()[0])'
  const done = spawnSync('uv', ['run', '--project', ROOT, 'python', '-c', read, path.join(home, 'atlas.db')], {
    cwd: ROOT,
    encoding: 'utf8',
    env: cleanEnvironment({}),
    windowsHide: true
  })

  return Number(done.stdout.trim())
}

test('a new conversation is made by its first message, not by a pick or "Auto" before it', async () => {
  desktop = await launch({ models: MODELS })
  const { home, model, page } = desktop
  const picker = page.getByRole('combobox', { name: /^Model/ })
  const auto = page.getByRole('button', { name: 'Auto mode' })

  await page.getByRole('button', { name: 'New conversation' }).click()
  await picker.selectOption({ label: 'Fake Other' })
  // On from the start (20/10/2026): turned off before the first message, it stays off.
  await expect(auto).toHaveAttribute('aria-pressed', 'true')
  await auto.click()
  await expect(auto).toHaveAttribute('aria-pressed', 'false')
  expect(stored(home)).toBe(0)

  await model.reply({ text: 'On the other, asking first.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toContainText('On the other, asking first.')

  expect(stored(home)).toBe(1)
  const requests = await model.requests()
  expect((requests[0]?.body as { model?: string }).model).toBe('fake-other')
  await expect(picker).toHaveValue('vllm\u0000fake-other')
  await expect(auto).toHaveAttribute('aria-pressed', 'false')

  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(picker).toHaveValue('vllm\u0000fake-model')
  await expect(auto).toHaveAttribute('aria-pressed', 'true')
  expect(stored(home)).toBe(1)
})
