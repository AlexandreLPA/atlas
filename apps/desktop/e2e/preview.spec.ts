import { writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, say } from './harness'

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

/** A page whose script tries every way into the app and says what it reached. */
const PROBE = [
  '<title>Probe</title>',
  '<p id="verdict">waiting</p>',
  '<script>',
  "let verdict = 'blocked'",
  "try { void parent.document.title; verdict = 'reached the window' } catch {}",
  "try { if (parent.atlas) verdict = 'reached the bridge' } catch {}",
  "try { localStorage.setItem('x', '1'); verdict = 'reached storage' } catch {}",
  "document.getElementById('verdict').textContent = verdict",
  '</script>'
].join('\n')

test('an HTML block opens beside only on its click, and its script runs without reaching the app', async () => {
  desktop = await launch()
  const { model, page } = desktop
  await model.reply({ text: `Here is the page:\n\n\`\`\`html\n${PROBE}\n\`\`\`` })
  await say(page, 'Make me a page.')

  const block = page.getByTestId('artifact-block')
  await expect(block).toBeVisible()
  // Never by itself.
  await expect(page.getByTestId('side-panel')).toBeHidden()
  await block.getByTestId('open-beside').click()

  await expect(page.getByTestId('preview-tab')).toContainText('Probe')
  const frame = page.frameLocator('[data-testid="preview-frame"]')
  // The script ran (the text changed), and reached neither the window, the bridge nor storage.
  await expect(frame.locator('#verdict')).toHaveText('blocked')
  await expect(page.getByTestId('preview-frame')).toHaveAttribute('sandbox', 'allow-scripts')
})

test('the agent shows a page beside its conversation, and a file it wrote waits as a link', async () => {
  desktop = await launch()
  const { folder, model, page } = desktop
  writeFileSync(path.join(folder, 'built.html'), '<h1 id="title">Built by the agent</h1>')
  await model.reply(
    { calls: [{ arguments: JSON.stringify({ query: 'preview pane' }), name: 'tool_search' }] },
    { calls: [{ arguments: JSON.stringify({ action: 'open', url: 'built.html' }), name: 'desktop_preview' }] },
    { text: 'It is beside you.' },
    {
      calls: [
        {
          arguments: JSON.stringify({ content: '<h1 id="title">The report</h1>', path: 'report.html' }),
          name: 'write_file'
        }
      ]
    },
    { text: 'I wrote report.html.' }
  )
  await say(page, 'Show me what you built.')

  // The agent asked: it opens, the conversation being on screen.
  await expect(page.getByTestId('preview-tab')).toContainText('built.html')
  await expect(page.frameLocator('[data-testid="preview-frame"]').locator('#title')).toHaveText('Built by the agent')

  await say(page, 'Now write the report.')
  await expect(page.getByTestId('assistant-message').last()).toContainText('I wrote report.html.')
  const link = page.getByTestId('preview-link').filter({ hasText: 'report.html' })
  await expect(link).toBeVisible()
  await expect(page.getByTestId('preview-tab')).toHaveCount(1)
  await link.getByRole('button', { name: /report\.html/ }).click()
  await expect(page.getByTestId('preview-tab')).toHaveCount(2)
  await expect(page.frameLocator('[data-testid="preview-frame"]').locator('#title')).toHaveText('The report')

  // Each tab is its conversation's: a new one shows none of them.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(page.getByTestId('preview-tab')).toHaveCount(0)
  await page.getByTestId('conversation-row').first().click()
  await expect(page.getByTestId('preview-tab')).toHaveCount(2)
})

test('a mermaid block opens beside as its drawing', async () => {
  desktop = await launch()
  const { model, page } = desktop
  await model.reply({ text: 'A flow:\n\n```mermaid\ngraph TD\n  A[Prompt] --> B[Answer]\n```' })
  await say(page, 'Draw it.')

  await page.getByTestId('open-beside').click()

  // Mermaid's own drawing, loaded when a diagram shows.
  await expect(page.getByTestId('preview-markdown').locator('svg[aria-roledescription]')).toBeVisible({
    timeout: 20_000
  })
  await expect(page.getByTestId('preview-markdown')).toContainText('Prompt')
})
