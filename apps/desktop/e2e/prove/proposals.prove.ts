/**
 * Step 9b proved against a real model through the app's window (`scripts/prove_proposals.py`
 * prepares the home and runs this). With `ATLAS_PROVE_REHEARSE=1` the model is the fake one,
 * scripted as the real one is expected to answer.
 *
 * A real model's words vary, so each step checks what must hold (a proposal came, the files did not
 * change before the approval, the stale proposal offers only to discard) and prints what it saw.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { type ElectronApplication, expect, type Page, test } from '@playwright/test'

import { FakeModel, openApp, pointAtFakeModel, type Reply, say, writeConfig } from '../harness'

const rehearse = process.env.ATLAS_PROVE_REHEARSE === '1'
const home = required('ATLAS_PROVE_HOME')
const work = required('ATLAS_PROVE_WORK')
const pace = Number(process.env.ATLAS_PROVE_PACE ?? '0') * 1000
const firstTitle = required('ATLAS_PROVE_FIRST_TITLE')
const secondTitle = required('ATLAS_PROVE_SECOND_TITLE')
/** A real model gets minutes; the fake one answers at once. */
const LONG = rehearse ? 20_000 : 600_000
// A review on a slow day: Gemini took 120 to 137 s per call on 11/10/2026, and one review ran past 5
// minutes still streaming, which the server's 120 s silence limit does not cut.
const REVIEW = rehearse ? 30_000 : 600_000
const ENTRY_DELIMITER = '\n§\n'

function required(name: string): string {
  const value = process.env[name]

  if (!value) {
    throw new Error(`${name} is not set; run scripts/prove_proposals.py`)
  }

  return value
}

function log(text: string): void {
  process.stdout.write(`${text}\n`)
}

const files = { memory: path.join(home, 'memory', 'MEMORY.md'), user: path.join(home, 'memory', 'USER.md') }

function read(): { memory: string; user: string } {
  return { memory: readFileSync(files.memory, 'utf8'), user: readFileSync(files.user, 'utf8') }
}

/** The files as the script seeded them: they must hold until an approval. */
const seeds = read()
/** What the user leaves after editing the files by hand: no entry a proposal recorded is there. */
const EDITED = {
  memory: 'Pasta de trabalho: ~/Ilustracao.',
  user: [
    'Nome: Marina Duarte, ilustradora de livros infantis.',
    'Mora em Lisboa.',
    'Prefere respostas curtas, em tópicos.'
  ].join(ENTRY_DELIMITER)
}

let fake: FakeModel | null = null

/** In a rehearsal, what the fake model answers next; nothing against a real model. */
async function script(...replies: Reply[]): Promise<void> {
  if (fake) {
    await fake.reply(...replies)
  }
}

const call = (name: string, args: Record<string, unknown>) => ({ arguments: JSON.stringify(args), name })
const flat = (text: string) => text.replace(/\s+/g, ' ').trim()

async function idle(page: Page): Promise<void> {
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden({ timeout: LONG })
}

/** Open a seeded conversation, send its tenth message, and wait for the review's row. */
async function review(page: Page, title: string, tenth: string): Promise<string> {
  await page.getByTestId('conversation-row').filter({ hasText: title }).click()
  await expect(page.getByTestId('user-message')).toHaveCount(9)
  await say(page, tenth)
  await expect(page.getByTestId('user-message').last()).toHaveText(tenth, { timeout: LONG })
  await idle(page)
  log(`  reply: ${JSON.stringify(flat(await page.getByTestId('assistant-message').last().innerText()))}`)
  // The seeded messages hold no tool: a row here is this turn's. A turn that writes the memory
  // itself leaves the review nothing to propose.
  const tools = await page.getByTestId('tool-row').allInnerTexts()
  log(`  tools in the turn: ${JSON.stringify(tools.map(flat))}`)
  const summary = page.getByTestId('review-summary')
  await expect(summary).toBeVisible({ timeout: REVIEW })

  return flat(await summary.innerText())
}

async function showCards(page: Page): Promise<number> {
  const cards = page.getByTestId('proposal-card')
  await expect(cards.first()).toBeVisible({ timeout: LONG })

  for (const text of await cards.allInnerTexts()) {
    log(`  card: ${JSON.stringify(flat(text))}`)
  }

  return cards.count()
}

type Step = (page: Page, app: ElectronApplication) => Promise<boolean>

const steps: [string, Step][] = [
  [
    '1 a conversation contradicts the full profile: the review proposes, and nothing changes',
    async page => {
      await script(
        { text: 'Roma.' },
        {
          calls: [
            call('memory', { action: 'add', content: 'Mudou-se para Lisboa em setembro de 2026.', target: 'user' })
          ]
        },
        {
          calls: [
            call('memory', {
              operations: [
                {
                  action: 'replace',
                  content: 'Mora em Lisboa desde setembro de 2026; trabalha num coworking no Cais do Sodré.',
                  old_text: 'Mora no Porto'
                },
                { action: 'remove', old_text: 'Planeja mudar do Porto' },
                {
                  action: 'replace',
                  content: 'Prefere respostas curtas, em tópicos.',
                  old_text: 'Prefere respostas longas'
                }
              ],
              target: 'user'
            })
          ]
        },
        { text: 'Proposed.' }
      )
      // A question that needs nothing from the memory: the conversation answers it, and the
      // review, now due, meets the earlier messages (the conversation may write memory itself, and
      // then the review has nothing to propose: Gemini 3.7 did on 11/10/2026).
      const said = await review(page, firstTitle, 'Uma pergunta rápida: qual a capital da Itália? Só o nome.')
      log(`  review: ${JSON.stringify(said)}`)
      const cards = await showCards(page)
      const offered = await page.getByTestId('review-summary').getByRole('button', { name: 'Review' }).count()
      const unchanged = JSON.stringify(read()) === JSON.stringify(seeds)
      log(`  ${String(cards)} card(s); the files are as seeded: ${String(unchanged)}`)

      return said.includes('change proposed') && offered === 1 && unchanged
    }
  ],
  [
    '2 approving the card applies it, and says what it overwrote or removed',
    async page => {
      const cards = page.getByTestId('proposal-card')
      await cards.first().getByRole('button', { name: 'Approve' }).click()
      const applied = page.getByTestId('proposal-applied')
      await expect(applied).toBeVisible({ timeout: LONG })
      const said = flat(await applied.innerText())
      log(`  applied: ${JSON.stringify(said)}`)
      const now = read()
      const changed = now.user !== seeds.user || now.memory !== seeds.memory
      // What it overwrote or removed was in the seeds, whole.
      const seeded = [...seeds.user.split(ENTRY_DELIMITER), ...seeds.memory.split(ENTRY_DELIMITER)]
      const named = seeded.filter(entry => said.includes(entry))
      log(`  the files changed: ${String(changed)}; seeded entries it names: ${JSON.stringify(named)}`)
      await page.getByRole('button', { name: 'OK' }).click()

      // Another proposal the same review made goes too: this step proves one approval.
      while ((await cards.count()) > 0) {
        const left = await cards.count()
        log(`  discarding another: ${JSON.stringify(flat(await cards.first().innerText()))}`)
        await cards.first().getByRole('button', { name: 'Discard' }).click()
        await expect(cards).toHaveCount(left - 1)
      }

      return changed && named.length > 0
    }
  ],
  [
    '3 a proposal whose entries the user changed by hand is marked, and only discarded',
    async page => {
      // The profile is full again, as the first review met it.
      writeFileSync(files.user, seeds.user)
      writeFileSync(files.memory, seeds.memory)
      await script(
        { text: 'Madri.' },
        {
          calls: [
            call('memory', {
              operations: [
                {
                  action: 'replace',
                  content: 'Nome: Marina Duarte. Ilustradora de livros infantis, freelancer.',
                  old_text: 'Designer gráfica há 8 anos'
                },
                {
                  action: 'replace',
                  content: 'Cliente principal: Editora Pirilampo (livros infantis).',
                  old_text: 'Cliente principal: Editora Atlântica'
                }
              ],
              target: 'user'
            })
          ]
        },
        { text: 'Proposed.' }
      )
      const said = await review(page, secondTitle, 'E a capital da Espanha? Só o nome.')
      log(`  review: ${JSON.stringify(said)}`)
      await showCards(page)
      writeFileSync(files.user, EDITED.user)
      writeFileSync(files.memory, EDITED.memory)
      log('  the user edited both files by hand')

      await page.getByTestId('proposals-entry').click()
      const view = page.getByTestId('proposals-view')
      const cards = view.getByTestId('proposal-card')
      await expect(cards.first()).toBeVisible()
      const count = await cards.count()
      let marked = 0

      for (let index = 0; index < count; index += 1) {
        const card = cards.nth(index)
        const problem = card.getByTestId('proposal-problem')
        const approve = await card.getByRole('button', { name: 'Approve' }).count()

        if ((await problem.count()) === 1 && approve === 0) {
          marked += 1
          log(`  marked: ${JSON.stringify(flat(await problem.innerText()))}`)
        }
      }

      while ((await cards.count()) > 0) {
        const left = await cards.count()
        await cards.first().getByRole('button', { name: 'Discard' }).click()
        await expect(cards).toHaveCount(left - 1)
      }

      await expect(view).toContainText('No memory proposals.')
      const kept = JSON.stringify(read()) === JSON.stringify(EDITED)
      log(`  ${String(marked)} of ${String(count)} marked; the files are as the user left them: ${String(kept)}`)

      return said.includes('change proposed') && count > 0 && marked === count && kept
    }
  ]
]

test('the review proposals against a real model', async () => {
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

      // Each step stands on the one before (2 approves 1's card), and a step cut short leaves its
      // turn running into the next: stop at the first that failed.
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
