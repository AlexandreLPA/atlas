/**
 * Step 10b proved through the app's window against a real model (`scripts/prove_homes.py` prepares
 * the default home and runs this): homes cloned and switched in the app, a turn in another home
 * that goes on and notifies, and a backup restored into a new home, with its conversations and
 * memory and without its keys. What a step needs done in the terminal (clone a home, back up and
 * restore, store a key), it asks the script for, as a user would type it. With
 * `ATLAS_PROVE_REHEARSE=1` the model is the fake one.
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { FakeModel, listedHomes, openApp, type Reply, say, switchHome } from '../harness'

const rehearse = process.env.ATLAS_PROVE_REHEARSE === '1'
const home = required('ATLAS_PROVE_HOME')
const work = required('ATLAS_PROVE_WORK')
const signals = required('ATLAS_PROVE_SIGNALS')
const keyName = required('ATLAS_PROVE_KEY_NAME')
const pace = Number(process.env.ATLAS_PROVE_PACE ?? '0') * 1000
/** A real model gets minutes; the fake one answers at once. */
const LONG = rehearse ? 20_000 : 180_000

function required(name: string): string {
  const value = process.env[name]

  if (!value) {
    throw new Error(`${name} is not set; run scripts/prove_homes.py`)
  }

  return value
}

function log(text: string): void {
  process.stdout.write(`${text}\n`)
}

let fake: FakeModel | null = null
/** In a turn's instructions and not in a title's: since 14/10 the title model is GPT-6 Luna
 * whenever OpenAI has a key, and in the rehearsal OpenAI is the fake, so an unmarked reply could
 * be taken by the title's call, which starts with the turn, and the turn would get none. */
const TURN = 'working folder is'

async function script(...replies: Reply[]): Promise<void> {
  if (fake) {
    await fake.reply(...replies.map(reply => ({ when: TURN, ...reply })))
  }
}

/** Ask the script to do something in the terminal, and wait until it is done. */
async function ask(what: 'backup' | 'clone' | 'key'): Promise<void> {
  writeFileSync(path.join(signals, `${what}.asked`), '')
  await expect
    .poll(() => existsSync(path.join(signals, `${what}.done`)), { intervals: [500], timeout: 10 * 60_000 })
    .toBe(true)
}

const call = (name: string, args: Record<string, unknown>) => ({ arguments: JSON.stringify(args), name })
const connected = (page: Page) => page.locator('[data-connection="open"]')
const lastReply = async (page: Page) => (await page.getByTestId('assistant-message').last().innerText()).trim()
const STORY = Array.from({ length: 40 }, (_, n) => `Frase ${String(n + 1)} sobre os azulejos.`).join(' ')

async function idle(page: Page): Promise<void> {
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: LONG })
}

async function turn(page: Page, text: string): Promise<void> {
  await say(page, text)
  await expect(page.getByTestId('user-message').last()).toHaveText(text, { timeout: LONG })
  await idle(page)
}

async function switchTo(page: Page, name: string): Promise<void> {
  await switchHome(page, name)
  await expect(connected(page)).toBeVisible({ timeout: 90_000 })
}

/** Back from the terminal: the window reads the homes again. */
async function comeBack(page: Page, name: string): Promise<void> {
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect.poll(() => listedHomes(page)).toContain(name)
}

type Step = (page: Page) => Promise<boolean>

const steps: [string, Step][] = [
  [
    '1 the default home keeps who the user is',
    async page => {
      await script(
        { calls: [call('memory', { action: 'add', content: 'Marina, designer de interiores.', target: 'user' })] },
        { text: 'Guardado no seu perfil.' }
      )
      await turn(page, 'Meu nome é Marina e trabalho com design de interiores. Guarde isso no meu perfil.')
      const profile = readFileSync(path.join(home, 'memory', 'USER.md'), 'utf8')
      log(`  USER.md: ${JSON.stringify(profile.trim())}; reply: ${JSON.stringify(await lastReply(page))}`)

      return profile.includes('Marina')
    }
  ],
  [
    '2 a home cloned in the terminal shows in the app',
    async page => {
      await ask('clone')
      await comeBack(page, 'trabalho')
      const homes = await listedHomes(page)
      log(`  homes: ${JSON.stringify(homes)}`)

      return homes.includes('trabalho')
    }
  ],
  [
    '3 the clone knows the user and has the keys, but none of the conversations',
    async page => {
      await switchTo(page, 'trabalho')
      const rows = await page.getByTestId('conversation-row').count()
      await script({ text: 'Você é a Marina e trabalha com design de interiores.' })
      await turn(page, 'Qual é o meu nome e com o que eu trabalho? Responda em uma frase.')
      const reply = await lastReply(page)
      log(`  ${String(rows)} conversations; reply: ${JSON.stringify(reply)}`)

      return rows === 0 && reply.includes('Marina')
    }
  ],
  [
    '4 a turn in another home goes on and notifies when it ends',
    async page => {
      await page.evaluate(() => {
        const seen: string[][] = []
        Object.assign(window, { seenNotifications: seen })
        window.Notification = class {
          onclick: (() => void) | null = null

          constructor(title: string, options?: NotificationOptions) {
            seen.push([title, options?.body ?? ''])
          }
        } as unknown as typeof Notification
      })
      await script({ pause: 0.02, text: STORY }, { text: 'ok' })
      await say(page, 'Escreva um texto de umas 600 palavras sobre azulejos portugueses.')
      await expect(page.getByTestId('assistant-message').last()).not.toBeEmpty({ timeout: LONG })
      await switchTo(page, 'default')
      await page.getByRole('button', { name: 'New conversation' }).click()
      await turn(page, 'Responda só: ok')
      const reply = await lastReply(page)
      await expect
        .poll(() => page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications), {
          timeout: LONG
        })
        .not.toEqual([])
      const seen = await page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications)
      log(`  default answered ${JSON.stringify(reply)}; notified: ${JSON.stringify(seen)}`)

      return seen.some(([title, body]) => title === 'Atlas answered' && (body ?? '').includes('(home trabalho)'))
    }
  ],
  [
    '5 a backup restored into a new home brings conversations and memory, never the keys',
    async page => {
      await ask('backup')
      await comeBack(page, 'copia')
      await switchTo(page, 'copia')
      const titles = await page.getByTestId('conversation-title').allInnerTexts()
      await turn(page, 'Qual é o meu nome?')
      // No key: the turn fails, and its end says how to store one (since step 9c, not a reply).
      const reply = (await page.getByTestId('turn-end').last().innerText()).trim()
      log(`  conversations: ${JSON.stringify(titles)}; turn end: ${JSON.stringify(reply)}`)

      return titles.length >= 2 && reply.includes('atlas secrets set') && reply.includes(keyName)
    }
  ],
  [
    '6 with its key stored, the restored home answers from the memory it brought',
    async page => {
      await ask('key')
      await script({ text: 'Marina.' })
      await turn(page, 'E agora, qual é o meu nome? Responda em uma palavra.')
      const reply = await lastReply(page)
      log(`  reply: ${JSON.stringify(reply)}`)

      return reply.includes('Marina')
    }
  ]
]

test('several homes, and a backup that moves one', async () => {
  if (rehearse) {
    fake = await FakeModel.start()
    // OpenAI pointed at the fake: the restored home needs a key as the real one does.
    writeFileSync(
      path.join(home, 'models.toml'),
      `[providers.openai]\nbase_url = ${JSON.stringify(`http://127.0.0.1:${String(fake.port)}/v1`)}\n`
    )
  }

  const { app, page } = await openApp(path.dirname(home), home)
  log(`work folder: ${work}`)
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
    fake?.stop()
  }

  for (const [name, ok] of results) {
    log(`${ok ? 'ok  ' : 'FAIL'} ${name}`)
  }

  expect(results.filter(([, ok]) => !ok)).toEqual([])
  expect(results).toHaveLength(steps.length)
})
