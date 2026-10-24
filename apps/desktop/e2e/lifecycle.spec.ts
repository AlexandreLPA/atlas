import { spawnSync } from 'node:child_process'

import { type ElectronApplication, expect, test } from '@playwright/test'

import { type Desktop, launch, running, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

/** The server the app started: its process (uv's, with the server under it) and its address. */
async function server(app: ElectronApplication): Promise<{ pid: number; url: string }> {
  return app.evaluate(async () => {
    const owned = (globalThis as unknown as { atlasServer: { endpoint: () => Promise<{ url: string }>; pid: number } })
      .atlasServer
    const { url } = await owned.endpoint()

    return { pid: owned.pid, url }
  })
}

async function answers(url: string): Promise<boolean> {
  const port = new URL(url).port

  try {
    const response = await fetch(`http://127.0.0.1:${port}/health`, { signal: AbortSignal.timeout(2_000) })

    return response.ok
  } catch {
    return false
  }
}

test('the server leaves with the app', async () => {
  const { pid, url } = await server(desktop.app)
  expect(running(pid)).toBe(true)
  expect(await answers(url)).toBe(true)

  await desktop.app.close()

  // The watchdog looks every 2 seconds, and the stop is graceful (at most 10 more).
  await expect.poll(() => running(pid), { timeout: 30_000 }).toBe(false)
  await expect.poll(() => answers(url), { timeout: 30_000 }).toBe(false)
})

test('a server that dies is started again, and the conversation goes on', async () => {
  const { model, page } = desktop
  await model.reply({ text: 'First answer.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toHaveText('First answer.')
  const before = await server(desktop.app)

  // Killed as a crash kills it: the whole tree, with no chance to clean up.
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(before.pid), '/t', '/f'], { windowsHide: true })
  } else {
    process.kill(-before.pid, 'SIGKILL')
  }

  await expect(page.locator('[data-connection="open"]')).toBeHidden({ timeout: 30_000 })
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })
  const after = await server(desktop.app)
  expect(after.url).not.toBe(before.url)

  // The new server read the conversation from the database, and it goes on.
  await expect(page.getByTestId('assistant-message')).toHaveText('First answer.')
  await model.reply({ text: 'Second answer.' })
  await say(page, 'Still there?')
  await expect(page.getByTestId('assistant-message').last()).toHaveText('Second answer.')
})
