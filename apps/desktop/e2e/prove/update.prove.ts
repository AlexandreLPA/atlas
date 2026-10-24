/**
 * The app's update proved for real (`scripts/prove_update.py` makes the installation, and runs
 * this): the real app of a temporary installation, not under the end-to-end flag, so its real
 * look runs (`python -m atlas.installation check`, fetching its own origin); its button is clicked
 * and the real hand-off takes over as it closes. What the hand-off did after is checked by the
 * script, which this cannot see: the app it opens again is not Playwright's.
 */
import { _electron, expect, test } from '@playwright/test'

function required(name: string): string {
  const value = process.env[name]

  if (!value) {
    throw new Error(`${name} is not set; run scripts/prove_update.py`)
  }

  return value
}

const electron = required('ATLAS_PROVE_ELECTRON')
const desktop = required('ATLAS_PROVE_DESKTOP')
const subject = required('ATLAS_PROVE_SUBJECT')
/** The app's own folder for the proof: Electron reads no `APPDATA`, and the user's app, open, holds
 * the single-instance lock of the folder it would take. The update opens the app again on it. */
const profile = required('ATLAS_PROVE_PROFILE')

test('the update button closes the real app, and the real hand-off takes over', async () => {
  const environment: Record<string, string> = {}

  for (const [name, value] of Object.entries(process.env)) {
    if (value !== undefined && name !== 'ELECTRON_RUN_AS_NODE') {
      environment[name] = value
    }
  }

  const app = await _electron.launch({
    args: [desktop, `--user-data-dir=${profile}`],
    cwd: desktop,
    env: environment,
    executablePath: electron
  })
  const page = await app.firstWindow()

  // The first look runs 10 seconds after the app opens, and fetches.
  await expect(page.getByTestId('update-button')).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId('update-count')).toHaveText('1')
  await page.getByTestId('update-button').click()
  await expect(page.getByTestId('update-commits')).toContainText(subject)
  process.stdout.write(`  the dialog: ${JSON.stringify(await page.getByTestId('update-dialog').innerText())}\n`)

  const closed = new Promise<void>(resolve => {
    app.once('close', () => {
      resolve()
    })
  })
  await page.getByTestId('update-apply').click()
  await closed
  process.stdout.write('  the app closed for the hand-off\n')
})
