/**
 * Step 16f proved through the app's window (`scripts/prove_tab.py` prepares the home, the
 * terminal session and the failing test, and runs this): a Claude Code session begun in a terminal
 * listed and continued here, and the play running a failing test that Atlas then explains. With
 * `ATLAS_PROVE_REHEARSE=1` the model and Claude Code are the fakes.
 *
 * A real model's words vary, so each step checks what must hold and prints what it saw.
 */
import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { FakeModel, openApp, pointAtFakeModel, type Reply, say, writeConfig } from '../harness'

const rehearse = process.env.ATLAS_PROVE_REHEARSE === '1'
const home = required('ATLAS_PROVE_HOME')
const work = required('ATLAS_PROVE_WORK')
const project = required('ATLAS_PROVE_PROJECT')
const word = required('ATLAS_PROVE_WORD')
const chosen = (process.env.ATLAS_PROVE_STEPS ?? '1,2').split(',')
/** A real model and Claude Code get minutes; the fakes answer at once. */
const LONG = rehearse ? 30_000 : 300_000

function required(name: string): string {
  const value = process.env[name]

  if (!value) {
    throw new Error(`${name} is not set; run scripts/prove_tab.py`)
  }

  return value
}

function log(text: string): void {
  process.stdout.write(`${text}\n`)
}

let fake: FakeModel | null = null

async function script(...replies: Reply[]): Promise<void> {
  if (fake) {
    await fake.reply(...replies)
  }
}

type Step = (page: Page) => Promise<boolean>

const steps: [string, Step][] = [
  [
    '1 a Claude Code session begun in a terminal is listed, opens, and goes on through Atlas',
    async page => {
      await page.getByTestId('tab-executors').click()
      await page.getByTestId('executor-card').filter({ hasText: 'Claude Code' }).click()
      const folder = page.locator(`[data-testid="session-folder"][data-folder="${project.replace(/\\/g, '\\\\')}"]`)
      await expect(folder).toBeVisible({ timeout: LONG })
      await folder.getByTestId('session-folder-toggle').click()
      const row = folder.getByTestId('session-row').first()
      log(`  listed: ${JSON.stringify(await row.innerText())}`)
      await row.click()
      await expect(page.getByTestId('user-message').first()).toContainText(word, { timeout: LONG })
      const box = page.getByTestId('session-box')
      await box.fill('Qual palavra eu pedi para você guardar? Responda só a palavra.')
      await box.press('Enter')
      const answer = page
        .getByTestId('assistant-message')
        .filter({ hasText: new RegExp(word, 'i') })
        .last()
      await expect(page.getByTestId('user-message').last()).toContainText('Qual palavra', { timeout: LONG })
      await expect(answer).toBeVisible({ timeout: LONG })
      log(`  answered: ${JSON.stringify(await answer.innerText())}`)

      return true
    }
  ],
  [
    '2 the play runs a failing test in the terminal, and Atlas says why it failed',
    async page => {
      await script(
        { text: 'Rode assim:\n\n```powershell\nnode soma.test.js\n```' },
        { text: 'O teste falha porque soma faz a - b: soma(2, 3) dá -1, e o teste espera 5.' }
      )
      await page.getByTestId('tab-conversations').click()
      await page.getByRole('button', { name: 'New conversation' }).click()
      await say(
        page,
        'Na pasta de trabalho há o arquivo soma.test.js. Não rode nada você mesmo: me dê só o comando para ' +
          'rodá-lo com node, num bloco de código powershell.'
      )
      const block = page.locator('[data-testid="shell-block"]').filter({ hasText: 'soma.test.js' }).first()
      await expect(block).toBeVisible({ timeout: LONG })
      await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: LONG })
      log(`  the block: ${JSON.stringify(await block.locator('pre').first().innerText())}`)
      await block.getByTestId('run-block').click()
      const confirm = page.getByTestId('run-confirm')

      if (await confirm.isVisible({ timeout: 2_000 }).catch(() => false)) {
        log(`  asked first: ${JSON.stringify(await confirm.innerText())}`)
        await confirm.getByRole('button', { name: 'Run it' }).click()
      }

      const row = page.getByTestId('terminal-row').last()
      await expect(row).toBeVisible({ timeout: LONG })
      log(`  terminal row: ${JSON.stringify(await row.innerText())}`)
      await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: LONG })
      const reply = (await page.getByTestId('assistant-message').last().innerText()).trim()
      log(`  Atlas: ${JSON.stringify(reply)}`)
      const exit = (
        await row
          .getByTestId('terminal-exit')
          .innerText()
          .catch(() => '')
      ).trim()
      const explains = /a\s*-\s*b|subtra|-1|menos/i.test(reply)

      return exit !== 'exit 0' && exit !== '' && explains
    }
  ]
]

test('step 16f through the window', async () => {
  if (rehearse) {
    fake = await FakeModel.start()
    pointAtFakeModel(home, fake)
    writeConfig(home, { model: 'fake-model', provider: 'vllm' }, work)
  } else {
    writeConfig(home, { model: required('ATLAS_PROVE_MODEL'), provider: required('ATLAS_PROVE_PROVIDER') }, work)
  }

  const { app, page } = await openApp(path.dirname(home), home)
  const results: [string, boolean][] = []

  try {
    for (const [name, step] of steps) {
      if (!chosen.includes(name.split(' ')[0] ?? '')) {
        continue
      }

      log(name)
      let ok = false

      try {
        ok = await step(page)
      } catch (error) {
        log(`  error: ${String(error).split('\n')[0] ?? ''}`)
      }

      results.push([name, ok])
    }
  } finally {
    await app.close().catch(() => undefined)
    fake?.stop()
  }

  for (const [name, ok] of results) {
    log(`${ok ? 'ok  ' : 'FAIL'} ${name}`)
  }

  expect(results.filter(([, ok]) => !ok)).toEqual([])
})
