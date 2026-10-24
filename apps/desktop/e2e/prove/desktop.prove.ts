/**
 * The desktop app proved end to end against a real model, through its window
 * (`scripts/prove_desktop.py` prepares the home and runs this). With `ATLAS_PROVE_REHEARSE=1`
 * the model is the fake one, scripted as the real one is expected to answer.
 *
 * A real model's words vary, so each step checks what must hold (the card came, the tool ran, the
 * answer names the fact) and prints what it saw.
 */
import { writeFileSync } from 'node:fs'
import path from 'node:path'

import { type ElectronApplication, expect, type Page, test } from '@playwright/test'

import { FakeModel, manual, openApp, pointAtFakeModel, type Reply, running, say, writeConfig } from '../harness'

const rehearse = process.env.ATLAS_PROVE_REHEARSE === '1'
const home = required('ATLAS_PROVE_HOME')
const work = required('ATLAS_PROVE_WORK')
const pace = Number(process.env.ATLAS_PROVE_PACE ?? '0') * 1000
const reviewTitle = required('ATLAS_PROVE_REVIEW_TITLE')
/** A real model gets minutes; the fake one answers at once. */
const LONG = rehearse ? 20_000 : 180_000

function required(name: string): string {
  const value = process.env[name]

  if (!value) {
    throw new Error(`${name} is not set; run scripts/prove_desktop.py`)
  }

  return value
}

function log(text: string): void {
  process.stdout.write(`${text}\n`)
}

let fake: FakeModel | null = null

/** In a rehearsal, what the fake model answers next; nothing against a real model. */
async function script(...replies: Reply[]): Promise<void> {
  if (fake) {
    await fake.reply(...replies)
  }
}

const call = (name: string, args: Record<string, unknown>) => ({ arguments: JSON.stringify(args), name })
const STORY = Array.from({ length: 60 }, (_, n) => `Frase ${n + 1} sobre o azulejo.`).join(' ')

async function idle(page: Page): Promise<void> {
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: LONG })
}

/** Send a message and wait for its turn to end: first its start (the user's row), then the end. */
async function turn(page: Page, text: string): Promise<void> {
  await say(page, text)
  await expect(page.getByTestId('user-message').last()).toHaveText(text, { timeout: LONG })
  await idle(page)
}

async function fresh(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(page.getByText('Ask Atlas anything.')).toBeVisible()
}

const lastReply = async (page: Page) => (await page.getByTestId('assistant-message').last().innerText()).trim()

type Step = (page: Page, app: ElectronApplication) => Promise<boolean>

const steps: [string, Step][] = [
  [
    '1 a turn arrives in pieces',
    async page => {
      await script({
        pause: 0.05,
        text: 'A capital de Portugal é Lisboa. Fica à beira do Tejo. Foi escolhida pelo porto.'
      })
      await fresh(page)
      // Sample the reply as it grows: more than one length seen means it came in pieces.
      await page.evaluate(() => {
        const seen = new Set<number>()
        Object.assign(window, { replyLengths: seen })
        setInterval(() => {
          const replies = document.querySelectorAll('[data-testid="assistant-message"]')
          const last = replies[replies.length - 1]

          if (last) {
            seen.add(last.textContent.length)
          }
        }, 20)
      })
      await turn(page, 'Em três frases curtas: qual a capital de Portugal, e por que ela foi escolhida?')
      const lengths = await page.evaluate(() => (window as unknown as { replyLengths: Set<number> }).replyLengths.size)
      const reply = await lastReply(page)
      log(`  ${String(lengths)} lengths seen while it arrived; reply: ${JSON.stringify(reply)}`)

      return lengths > 1 && reply.toLowerCase().includes('lisboa')
    }
  ],
  [
    "2 ask_user's card",
    async page => {
      await script(
        {
          calls: [call('ask_user', { questions: [{ choices: ['Azul', 'Verde'], question: 'Qual cor você prefere?' }] })]
        },
        { text: 'A parede será verde.' }
      )
      await fresh(page)
      await say(
        page,
        'Vou pintar a parede. Antes de escolher, me pergunte com a ask_user qual cor eu prefiro, azul ou verde, ' +
          'e depois me diga em uma frase qual será a cor.'
      )
      const card = page.getByTestId('ask-card')
      await expect(card).toBeVisible({ timeout: LONG })
      const choices = await card.getByRole('button').allInnerTexts()
      log(
        `  asked: ${JSON.stringify(await card.locator('legend').allInnerTexts())}, choices ${JSON.stringify(choices)}`
      )
      const green = choices.find(choice => choice.toLowerCase().includes('verde'))

      if (green) {
        await card.getByRole('button', { exact: true, name: green }).click()
      } else {
        await card.getByRole('button', { name: 'Other' }).first().click()
        await card.getByRole('textbox').first().fill('Verde')
      }

      await card.getByRole('button', { name: 'Answer' }).click()
      await idle(page)
      const reply = await lastReply(page)
      log(`  reply: ${JSON.stringify(reply)}`)

      return reply.toLowerCase().includes('verde')
    }
  ],
  [
    "3 an approval card for an untrusted server's tool",
    async page => {
      await script(
        { calls: [call('tool_search', { query: 'notes add note' })] },
        { calls: [call('mcp__notes__add_note', { text: 'comprar tinta verde' })] },
        { text: 'Anotei: comprar tinta verde.' }
      )
      await fresh(page)
      await manual(page)
      await say(
        page,
        'Use a ferramenta de notas (servidor MCP notes) para anotar exatamente: comprar tinta verde. ' +
          'Depois confirme em uma frase.'
      )
      const card = page.getByTestId('approval-card')
      await expect(card).toBeVisible({ timeout: LONG })
      log(`  card: ${JSON.stringify((await card.innerText()).split('\n').slice(0, 3))}`)
      await card.getByRole('button', { name: 'Allow once' }).click()
      await idle(page)
      const tool = page.locator('[data-testid="tool-row"]', { hasText: 'mcp__notes__add_note' }).last()
      await tool.locator('summary').click()
      const result = await tool.innerText()
      log(`  tool: ${JSON.stringify(result.replace(/\s+/g, ' ').slice(0, 160))}`)

      return (await tool.getAttribute('data-state')) === 'done' && result.includes('added note')
    }
  ],
  [
    '4 "Auto", on from the start, skips the card',
    async page => {
      await script(
        { calls: [call('tool_search', { query: 'notes add note' })] },
        { calls: [call('mcp__notes__add_note', { text: 'comprar pincel' })] },
        { text: 'Anotei: comprar pincel.' }
      )
      await fresh(page)
      await page.evaluate(() => {
        Object.assign(window, { sawApproval: false })
        new MutationObserver(() => {
          if (document.querySelector('[data-testid="approval-card"]')) {
            Object.assign(window, { sawApproval: true })
          }
        }).observe(document.body, { childList: true, subtree: true })
      })
      // On from the start (20/10/2026).
      await expect(page.getByRole('button', { name: 'Auto mode' })).toHaveAttribute('aria-pressed', 'true')
      await say(page, 'Use a ferramenta de notas (servidor MCP notes) para anotar exatamente: comprar pincel.')
      await idle(page)
      const card = await page.evaluate(() => (window as unknown as { sawApproval: boolean }).sawApproval)
      const tool = page.locator('[data-testid="tool-row"]', { hasText: 'mcp__notes__add_note' }).last()
      const state = await tool.getAttribute('data-state')
      log(`  approval card shown: ${String(card)}; add_note ${String(state)}`)

      return !card && state === 'done'
    }
  ],
  [
    '5 stopping, with the unsent message back in the box',
    async page => {
      await script({ pause: 0.1, text: STORY }, { text: 'OK' })
      await fresh(page)
      await say(page, 'Escreva um texto de 400 palavras sobre a história do azulejo português.')
      await expect(page.getByTestId('assistant-message')).toBeVisible({ timeout: LONG })
      await say(page, 'e termine com um poema')
      await page.getByRole('button', { name: 'Stop' }).click()
      await expect(page.getByTestId('turn-end')).toHaveText('Stopped.', { timeout: 60_000 })
      const box = page.getByRole('textbox', { name: 'Message Atlas' })
      const back = await box.inputValue()
      log(`  stopped; the box holds ${JSON.stringify(back)}`)
      await box.fill('')
      await turn(page, 'Esquece. Responda só: OK')
      const reply = await lastReply(page)
      log(`  next turn: ${JSON.stringify(reply)}`)

      return back.includes('termine com um poema') && reply.toLowerCase().includes('ok')
    }
  ],
  [
    '6 a message sent mid-turn',
    async page => {
      writeFileSync(path.join(work, 'lista.txt'), 'maçã\nbanana\nuva\n', 'utf8')
      await script(
        { calls: [call('read_file', { path: 'lista.txt' })], pause: 0.2, text: 'Vou ler o arquivo agora.' },
        { text: 'The fruits are apple, banana and grape.' }
      )
      await fresh(page)
      await say(page, 'Leia o arquivo lista.txt com read_file e me diga as frutas.')
      await expect(page.getByTestId('user-message')).toBeVisible()
      await say(page, 'Responda em inglês, por favor.')
      await idle(page)
      // The model may have answered before the message entered: then it started the next turn.
      await page.waitForTimeout(1_000)
      await idle(page)
      const entered = await page.getByText('sent during the turn').count()
      const reply = (await lastReply(page)).toLowerCase()
      log(`  entered the turn: ${String(entered > 0)}; reply: ${JSON.stringify(reply)}`)
      const tools = await page.locator('[data-testid="tool-row"]', { hasText: 'read_file' }).count()

      return tools > 0 && ['apple', 'grape'].some(word => reply.includes(word))
    }
  ],
  [
    '7 a dropped connection loses nothing',
    async page => {
      await script({ pause: 0.1, text: 'Lisboa\n\nPorto\n\nCoimbra\n\nBraga\n\nFaro' })
      await fresh(page)
      await say(page, 'Liste cinco cidades portuguesas, uma por linha, sem mais nada.')
      await expect(page.getByTestId('assistant-message')).toBeVisible({ timeout: LONG })
      const mid = await page.getByRole('button', { name: 'Stop' }).isVisible()
      await page.evaluate(() => {
        window.atlasE2e?.reconnect()
      })
      await idle(page)
      const live = await lastReply(page)
      const pieces = await page.getByTestId('assistant-message').count()
      // Read it again from the server: what the screen painted live must be what was stored.
      await page.getByTestId('conversation-row').nth(1).click()
      await page.getByTestId('conversation-row').first().click()
      await expect(page.getByTestId('assistant-message')).toHaveCount(1)
      const stored = await lastReply(page)
      log(`  dropped ${mid ? 'mid-turn' : 'after the turn'}; live ${JSON.stringify(live)}`)

      return pieces === 1 && live === stored && (await page.getByTestId('user-message').count()) === 1
    }
  ],
  [
    "8 the review's summary",
    async page => {
      await script(
        { text: 'Que tal "Verde e Cinza: design editorial"?' },
        {
          calls: [
            call('memory', { action: 'add', content: 'Marina, designer gráfica; respostas curtas.', target: 'user' })
          ]
        },
        { text: 'Saved.' }
      )
      await page.getByTestId('conversation-row').filter({ hasText: reviewTitle }).click()
      await expect(page.getByTestId('user-message')).toHaveCount(9)
      await turn(page, 'Pode me sugerir um título para o portfólio?')
      const summary = page.getByTestId('review-summary')
      await expect(summary).toBeVisible({ timeout: rehearse ? 30_000 : 300_000 })
      log(`  review: ${JSON.stringify(await summary.innerText())}`)

      return true
    }
  ],
  [
    '9 a system notification for a turn not on screen',
    async page => {
      await script({ pause: 0.3, text: 'pronto' })
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
      await fresh(page)
      await say(page, 'Responda só: pronto')
      await page.getByRole('button', { name: 'New conversation' }).click()
      await expect
        .poll(() => page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications), {
          timeout: LONG
        })
        .not.toEqual([])
      const seen = await page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications)
      log(`  notified: ${JSON.stringify(seen)}`)

      return seen[0]?.[0] === 'Atlas answered'
    }
  ],
  [
    '10 the server leaves with the app',
    async (_page, app) => {
      const pid = await app.evaluate(() => (globalThis as unknown as { atlasServer: { pid: number } }).atlasServer.pid)
      await app.close()
      await expect.poll(() => running(pid), { timeout: 30_000 }).toBe(false)
      log(`  server ${String(pid)} gone`)

      return true
    }
  ]
]

test('the desktop app against a real model', async () => {
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
      if (pace) {
        await page.waitForTimeout(pace)
      }

      log(name)
      let ok = false

      try {
        ok = await step(page, app)
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
