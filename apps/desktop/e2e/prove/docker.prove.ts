/**
 * Principle 4 proved through the app's window: an empty container, an API key and a working
 * conversation (`scripts/prove_docker.py` starts the container and runs this). The app reaches the
 * container with `ATLAS_DESKTOP_URL` and `ATLAS_DESKTOP_TOKEN`; what a step needs done to the
 * container (the key, a restart) it asks the script for, so the key never reaches this process.
 * With `ATLAS_PROVE_REHEARSE=1` the model is the fake one beside the container.
 *
 * A real model's words vary, so each step checks what must hold and prints what it saw.
 */
import { existsSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { openApp, type Reply, say } from '../harness'

const rehearse = process.env.ATLAS_PROVE_REHEARSE === '1'
const url = required('ATLAS_PROVE_URL')
const token = required('ATLAS_PROVE_TOKEN')
const signals = required('ATLAS_PROVE_SIGNALS')
const profile = required('ATLAS_PROVE_PROFILE')
const keyName = required('ATLAS_PROVE_KEY_NAME')
const fakeModel = process.env.ATLAS_PROVE_FAKE_MODEL
const pace = Number(process.env.ATLAS_PROVE_PACE ?? '0') * 1000
/** A real model gets minutes; the fake one answers at once. */
const LONG = rehearse ? 20_000 : 180_000

function required(name: string): string {
  const value = process.env[name]

  if (!value) {
    throw new Error(`${name} is not set; run scripts/prove_docker.py`)
  }

  return value
}

function log(text: string): void {
  process.stdout.write(`${text}\n`)
}

/** In a rehearsal, what the fake model beside the container answers next. */
async function script(...replies: Reply[]): Promise<void> {
  if (rehearse && fakeModel) {
    const response = await fetch(`${fakeModel}/_fake/reply`, {
      body: JSON.stringify(replies),
      headers: { 'content-type': 'application/json' },
      method: 'POST'
    })
    expect(response.ok).toBe(true)
  }
}

/** Ask the script to do something to the container, and wait until it is done. */
async function ask(what: 'key' | 'restart'): Promise<void> {
  writeFileSync(path.join(signals, `${what}.asked`), '')
  await expect
    .poll(() => existsSync(path.join(signals, `${what}.done`)), { intervals: [500], timeout: 10 * 60_000 })
    .toBe(true)
}

const call = (name: string, args: Record<string, unknown>) => ({ arguments: JSON.stringify(args), name })
const connected = (page: Page) => page.locator('[data-connection="open"]')
const lastReply = async (page: Page) => (await page.getByTestId('assistant-message').last().innerText()).trim()

/** Send a message and wait for its turn to end. */
async function turn(page: Page, text: string): Promise<void> {
  await say(page, text)
  await expect(page.getByTestId('user-message').last()).toHaveText(text, { timeout: LONG })
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: LONG })
}

type Step = (page: Page) => Promise<boolean>

const steps: [string, Step][] = [
  [
    '1 the app reaches the empty container',
    async page => {
      await expect(connected(page)).toBeVisible()
      const rows = await page.getByTestId('conversation-row').count()
      log(`  ${url}, ${String(rows)} conversations`)

      return rows === 0
    }
  ],
  [
    '2 with no key, a message says how to store one',
    async page => {
      await turn(page, 'Oi! Você está aí?')
      const reply = await lastReply(page)
      const end = await page.getByTestId('turn-end').last().innerText()
      log(`  reply: ${JSON.stringify(reply)}; ${JSON.stringify(end)}`)

      // The hint names every provider's key, OpenAI's first.
      return reply.includes('atlas secrets set') && reply.includes(keyName)
    }
  ],
  [
    '3 the key enters while the server runs',
    async page => {
      await ask('key')
      await expect(connected(page)).toBeVisible({ timeout: 60_000 })

      return true
    }
  ],
  [
    '4 a tool runs inside the container',
    async page => {
      await script(
        { calls: [call('run_command', { command: 'uname -s && whoami && echo atlas > notas.txt' })] },
        { text: 'O sistema é Linux, e o usuário é atlas.' }
      )
      await turn(
        page,
        'Use o run_command para rodar `uname -s && whoami` e para gravar a palavra atlas no arquivo ' +
          'notas.txt da pasta de trabalho. Depois me diga, em uma frase, o sistema e o usuário.'
      )
      const tools = page.getByTestId('tool-row')
      const ran = await tools.allInnerTexts()
      const states = await tools.evaluateAll(rows => rows.map(row => row.getAttribute('data-state')))
      const reply = await lastReply(page)
      log(
        `  tools: ${JSON.stringify(ran.map(text => text.replace(/\s+/g, ' ').slice(0, 120)))} ${JSON.stringify(states)}`
      )
      log(`  reply: ${JSON.stringify(reply)}`)

      return (
        ran.some(text => text.includes('run_command')) &&
        states.every(state => state === 'done') &&
        reply.toLowerCase().includes('linux')
      )
    }
  ],
  [
    '5 a restart keeps the conversation and the file',
    async page => {
      const before = await page.getByTestId('user-message').count()
      await ask('restart')
      await expect(connected(page)).toBeVisible({ timeout: 60_000 })
      // The new epoch made the window read the conversation again from the server.
      await expect(page.getByTestId('user-message')).toHaveCount(before, { timeout: 30_000 })
      await script({ calls: [call('read_file', { path: 'notas.txt' })] }, { text: 'Está escrito atlas.' })
      await turn(page, 'Leia o notas.txt e me diga, em uma frase, o que está escrito nele.')
      const reply = await lastReply(page)
      const tools = await page.getByTestId('tool-row').allInnerTexts()
      const read = tools.map(text => text.replace(/\s+/g, ' ').slice(0, 80))
      log(`  ${String(before)} messages of mine still there; tools now ${JSON.stringify(read)}`)
      log(`  reply: ${JSON.stringify(reply)}`)

      return reply.toLowerCase().includes('atlas')
    }
  ]
]

test('an empty container, a key and a conversation', async () => {
  const { app, page } = await openApp(profile, path.join(profile, 'unused-home'), {
    ATLAS_DESKTOP_TOKEN: token,
    ATLAS_DESKTOP_URL: url
  })
  const results: [string, boolean][] = []

  try {
    for (const [name, step] of steps) {
      if (pace) {
        await page.waitForTimeout(pace)
      }

      log(name)
      let ok = false

      try {
        ok = await step(page)
      } catch (error) {
        log(`  error: ${String(error).split('\n')[0] ?? ''}`)
      }

      results.push([name, ok])

      if (!ok) {
        break
      }
    }
  } finally {
    await app.close().catch(() => undefined)
  }

  for (const [name, ok] of results) {
    log(`${ok ? 'ok  ' : 'FAIL'} ${name}`)
  }

  expect(results.filter(([, ok]) => !ok)).toEqual([])
  expect(results).toHaveLength(steps.length)
})
