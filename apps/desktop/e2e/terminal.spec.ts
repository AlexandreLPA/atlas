import { expect, test } from '@playwright/test'

import { type Desktop, launch, say } from './harness'

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

/** `echo` means the same in PowerShell and in bash: the test runs on both. */
const ANSWER = [
  'Run this:',
  '',
  '```powershell',
  'echo atlas-ran',
  '```',
  '',
  'And this is Python, which does not run here:',
  '',
  '```python',
  'print("no")',
  '```'
].join('\n')

test('the play on a shell block runs it in the terminal, and Atlas answers its output', async () => {
  desktop = await launch()
  const { model, page } = desktop
  await model.reply(
    // Slow, so the block can be seen while the answer still comes.
    { pause: 0.15, text: ANSWER },
    { text: 'It printed atlas-ran: it works.' }
  )
  await say(page, 'How do I check it?')

  const shell = page.getByTestId('shell-block').filter({ hasText: 'echo atlas-ran' })
  // A block still coming does not run: half a command would run cut.
  await expect(shell.getByTestId('run-block')).toBeDisabled()
  await expect(page.getByTestId('assistant-message').last()).toContainText('does not run here')
  await expect(page.getByRole('status', { name: 'Working' })).toBeHidden()
  // Only a shell's block gets the play.
  await expect(page.locator('[data-testid="shell-block"][data-language="python"]')).toHaveCount(0)

  // Clicked with no terminal open yet: it waits for the shell's prompt and runs all the same.
  await shell.getByTestId('run-block').click()
  await expect(page.getByTestId('side-panel')).toBeVisible()
  await expect(page.getByTestId('terminal-tab')).toHaveCount(1)
  const row = page.getByTestId('terminal-row')
  await expect(row).toContainText('$ echo atlas-ran')
  await expect(row.getByTestId('terminal-output')).toHaveText('atlas-ran')
  await expect(row.getByTestId('terminal-exit')).toHaveText('exit 0')

  // Atlas answers what came out, in a turn that ends: the conversation is not left running.
  await expect(page.getByTestId('assistant-message').last()).toContainText('It printed atlas-ran')
  await expect(page.getByRole('status', { name: 'Working' })).toBeHidden()
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden()
  const asked = await model.seen()
  expect(asked).toContain('<bash-input>echo atlas-ran</bash-input>')
})

test('a dangerous block asks first and runs nothing when refused; the ! runs at once', async () => {
  desktop = await launch()
  const { model, page } = desktop
  await model.reply({ text: 'To start over:\n\n```bash\ngit reset --hard\n```' }, { text: 'bang-ran came out.' })
  await say(page, 'How do I throw my changes away?')

  const shell = page.getByTestId('shell-block').filter({ hasText: 'git reset --hard' })
  await expect(shell.getByTestId('run-block')).toBeEnabled()
  await shell.getByTestId('run-block').click()
  const confirm = page.getByTestId('run-confirm')
  await expect(confirm).toContainText('git reset --hard')
  await expect(confirm).toContainText('destroys uncommitted changes')
  await confirm.getByRole('button', { name: 'Cancel' }).click()
  await expect(confirm).toBeHidden()
  await expect(page.getByTestId('terminal-row')).toHaveCount(0)

  // The user wrote it: no confirmation, and no model call before it runs.
  await say(page, '!echo bang-ran')
  const row = page.getByTestId('terminal-row')
  await expect(row).toContainText('$ echo bang-ran')
  await expect(row.getByTestId('terminal-output')).toHaveText('bang-ran')
  await expect(page.getByTestId('run-confirm')).toHaveCount(0)
  await expect(page.getByTestId('assistant-message').last()).toContainText('bang-ran came out.')
  await expect(page.getByTestId('user-message').filter({ hasText: 'bang-ran' })).toHaveCount(0)
})

test('a command still running after the wait is said so, and its end later reaches Atlas again', async () => {
  // The wait is 30 seconds; the test shortens it (only under --atlas-e2e).
  desktop = await launch({ environment: { ATLAS_E2E_TERMINAL_WAIT_MS: '2000' } })
  const { model, page } = desktop
  await model.reply({ text: 'The server is starting.' }, { text: 'It stopped: exit 4.' })

  // `!` asks nothing, and node means the same in every shell.
  await say(page, `!node -e "console.log('up'); setTimeout(() => { console.log('down'); process.exit(4) }, 6000)"`)

  const rows = page.getByTestId('terminal-row')
  // The shell opens first (a few seconds for PowerShell through the server's ConPTY), then the wait.
  await expect(rows.first().getByTestId('terminal-state')).toContainText('still running after 30 seconds', {
    timeout: 20_000
  })
  await expect(rows.first().getByTestId('terminal-output')).toHaveText('up')
  await expect(page.getByTestId('assistant-message').last()).toContainText('The server is starting.')
  await expect(page.getByRole('status', { name: 'Working' })).toBeHidden()

  // It ended later: a row says so, and Atlas answers again (decided with the user on 15/10/2026).
  await expect(rows).toHaveCount(2, { timeout: 20_000 })
  await expect(rows.last().getByTestId('terminal-state')).toContainText('has ended')
  await expect(rows.last().getByTestId('terminal-exit')).toHaveText('exit 4')
  await expect(rows.last().getByTestId('terminal-output')).toContainText('down')
  await expect(page.getByTestId('assistant-message').last()).toContainText('It stopped: exit 4.')
})
