/**
 * The voice plugin in the real app (step 17a): turned on from the terminal, its screen adds the
 * box's two buttons; the microphone dictates into the box, through the plugin's Python and the fake
 * model server's transcription; the live conversation asks the plugin's Python for its session,
 * which reaches OpenAI's session endpoint with the app's WebRTC offer.
 *
 * Chromium's fake microphone stands in for a real one. The voice itself, spoken and heard over
 * WebRTC, needs OpenAI: the fake answers no real WebRTC, so the conversation ends there, saying why.
 */
import { spawnSync } from 'node:child_process'

import { expect, test } from '@playwright/test'

import { atlas, cleanEnvironment, type Desktop, type FakeModel, launch, ROOT } from './harness'

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

/** The voice plugin on, pointed at the fake model server, as a user does from a terminal. */
function voiceOn(home: string, model: FakeModel): void {
  atlas(home, 'plugins', 'set', 'voice', 'api_url', `http://127.0.0.1:${String(model.port)}/v1`)
  const stored = spawnSync('uv', ['run', '--project', ROOT, 'atlas', 'plugins', 'secret', 'voice', 'OPENAI_API_KEY'], {
    cwd: ROOT,
    encoding: 'utf8',
    env: cleanEnvironment({ ATLAS_HOME: home }),
    input: 'fake-key\n',
    windowsHide: true
  })
  expect(stored.status, stored.stderr).toBe(0)
  atlas(home, 'plugins', 'enable', 'voice', '--yes')
}

const MICROPHONE = ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream']

test('the box dictates through the voice plugin, and the words wait there to be sent', async () => {
  desktop = await launch({ chromium: MICROPHONE, setup: voiceOn })
  const { model, page } = desktop
  await model.transcribe('Remind me to call the dentist on Thursday.')

  const dictate = page.getByTestId('voice-dictate')
  await expect(dictate).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('voice-talk')).toBeVisible()

  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  await box.fill('Please:')
  await dictate.click()
  await expect(dictate).toHaveAttribute('data-state', 'recording')
  await page.waitForTimeout(800)
  await dictate.click()

  await expect(box).toHaveValue('Please: Remind me to call the dentist on Thursday.', { timeout: 30_000 })
  expect((await model.requests()).some(request => request.path === '/v1/audio/transcriptions')).toBe(true)
  // Dictated, not sent: no turn started.
  expect((await model.requests()).some(request => request.path === '/v1/chat/completions')).toBe(false)
})

test('the live conversation opens its session through the plugin, the key kept on the server', async () => {
  desktop = await launch({ chromium: MICROPHONE, setup: voiceOn })
  const { model, page } = desktop

  const talk = page.getByTestId('voice-talk')
  await expect(talk).toBeVisible({ timeout: 30_000 })
  await talk.click()

  // The fake answers with no real WebRTC: the session is asked for, then the conversation ends.
  await expect
    .poll(async () => (await model.requests()).filter(request => request.path === '/v1/live/sessions').length, {
      timeout: 30_000
    })
    .toBe(1)
  const [session] = (await model.requests()).filter(request => request.path === '/v1/live/sessions')
  const body = session?.body as unknown as { session: { model: string }; transport: { sdp: string; type: string } }
  expect(body.session.model).toBe('gpt-live-1')
  expect(body.transport.type).toBe('webrtc')
  expect(body.transport.sdp).toMatch(/^v=0/)
  expect(session?.authorization).toBe('Bearer fake-key')
  await expect(talk).toHaveAttribute('data-state', 'idle', { timeout: 30_000 })
})
