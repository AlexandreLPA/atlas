/**
 * The homes from the app (step 10c, docs/ARCHITECTURE.md, Homes in the app): the home is shown with
 * only one, a home made in the app opens and talks, a backup goes where the save dialog said, a
 * backup imports as a new home that opens with its conversation, and the app opens on the last
 * home it showed. The system's dialogs
 * are answered here; the app, the server, the files and the zip are real.
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import {
  atlas,
  type Desktop,
  editHome,
  homeButton,
  launch,
  listedHomes,
  newHomeFromMenu,
  openApp,
  openSettings,
  say,
  switchHome
} from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

/** Answer the system's save and open dialogs with `file` (`null`: the user cancels), and keep the
 * name each save dialog offered. */
async function answerDialogs(file: null | string): Promise<void> {
  await desktop.app.evaluate(({ dialog }, chosen) => {
    const offered: string[] = []
    Object.assign(globalThis, { offeredPaths: offered })
    dialog.showSaveDialog = (...args: unknown[]) => {
      const options = args.at(-1) as { defaultPath?: string }
      offered.push(options.defaultPath ?? '')

      return Promise.resolve(chosen ? { canceled: false, filePath: chosen } : { canceled: true, filePath: '' })
    }
    dialog.showOpenDialog = () =>
      Promise.resolve(chosen ? { canceled: false, filePaths: [chosen] } : { canceled: true, filePaths: [] })
  }, file)
}

const offeredPaths = () =>
  desktop.app.evaluate(() => (globalThis as unknown as { offeredPaths: string[] }).offeredPaths)

const homesServed = () =>
  desktop.app.evaluate(() => [
    ...(globalThis as unknown as { atlasServers: Map<null | string, unknown> }).atlasServers.keys()
  ])

test('the home shows with only one, and a home made in the app opens and talks', async () => {
  const { model, page } = desktop
  const home = homeButton(page)
  await expect(home).toHaveAttribute('data-home', 'default')
  expect(await listedHomes(page)).toEqual(['default'])

  await newHomeFromMenu(page)
  const dialog = page.getByTestId('home-dialog')
  // Copying starts on the default home.
  await expect(dialog.getByTestId('home-clone')).toHaveValue('default')
  await dialog.getByTestId('home-name').fill('Work Trips')
  await expect(dialog.getByText('work-trips')).toBeVisible()
  await dialog.getByTestId('home-submit').click()

  await expect(dialog).toBeHidden()
  await expect(home).toHaveAttribute('data-home', 'work-trips')
  expect(await listedHomes(page)).toEqual(['default', 'work-trips'])
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })
  expect(await homesServed()).toEqual([null, 'work-trips'])
  // A copy carries the model, so it answers at once.
  await model.reply({ text: 'Answered in the new home.' })
  await say(page, 'Hello from the new home')
  await expect(page.getByTestId('assistant-message')).toHaveText('Answered in the new home.')

  // A name a home has is refused, and the dialog says why.
  await newHomeFromMenu(page)
  await dialog.getByTestId('home-name').fill('work-trips')
  await dialog.getByTestId('home-submit').click()
  await expect(dialog.getByTestId('home-error')).toContainText('already a home')
})

test('a backup goes where the save dialog said, and imports as a new home that opens', async () => {
  const { model, page } = desktop
  await model.reply({ text: 'The blue door, noted.' })
  await say(page, 'Remember the blue door')
  await expect(page.getByTestId('assistant-message')).toHaveText('The blue door, noted.')

  // From the home's own dialog. Cancelled: nothing happens.
  await answerDialogs(null)
  const profile = await editHome(page)
  await expect(profile.getByTestId('home-profile-name')).toHaveText('default')
  await profile.getByTestId('home-backup').click()
  await expect.poll(offeredPaths).toHaveLength(1)
  await expect(page.getByTestId('home-notice')).toHaveCount(0)

  const backup = path.join(desktop.folder, 'my backups', 'atlas.zip')
  await answerDialogs(backup)
  await profile.getByTestId('home-backup').click()

  const notice = page.getByTestId('home-notice')
  await expect(notice).toHaveAttribute('data-kind', 'backed-up', { timeout: 30_000 })
  await expect(notice).toContainText(backup)
  expect(existsSync(backup)).toBe(true)
  // The dialog offered `atlas backup`'s name, in the user's home folder.
  const [offered] = await offeredPaths()
  expect(path.dirname(offered ?? '')).toBe(homedir())
  expect(path.basename(offered ?? '')).toMatch(/^atlas-backup-default-\d{8}T\d{6}Z\.zip$/)

  // Import: checked, then named; the default home exists, so a dated name is offered.
  await profile.getByTestId('home-import').click()
  await expect(profile).toBeHidden()
  const dialog = page.getByTestId('home-dialog')
  await expect(dialog.getByTestId('home-import-from')).toContainText('default')
  await expect(dialog.getByTestId('home-import-from')).toContainText('1 conversation')
  await expect(dialog.getByTestId('home-name')).toHaveValue(/^default-\d{4}-\d{2}-\d{2}$/)
  await dialog.getByTestId('home-name').fill('restored')
  await dialog.getByTestId('home-submit').click()

  await expect(homeButton(page)).toHaveAttribute('data-home', 'restored')
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })
  await expect(page.getByTestId('conversation-title')).toHaveText(['Remember the blue door'])
  await page.getByTestId('conversation-row').click()
  await expect(page.getByTestId('assistant-message')).toHaveText('The blue door, noted.')
  // The home it came from is as it was.
  await switchHome(page, 'default')
  await expect(page.getByTestId('conversation-title')).toHaveText(['Remember the blue door'])

  // What is not a backup is refused before anything is made.
  await answerDialogs(path.join(desktop.folder, 'my backups'))
  await (await editHome(page)).getByTestId('home-import').click()
  await expect(notice).toHaveAttribute('data-kind', 'failed')
  expect(await listedHomes(page)).toEqual(['default', 'restored'])
})

test('the app opens on the last home it showed, until that home is gone', async () => {
  const root = path.dirname(desktop.home)
  await newHomeFromMenu(desktop.page)
  await desktop.page.getByTestId('home-name').fill('work')
  await desktop.page.getByTestId('home-submit').click()
  await expect(homeButton(desktop.page)).toHaveAttribute('data-home', 'work')
  await desktop.app.close()

  // Kept in the app's own folder, the test's here, never the user's.
  expect(existsSync(path.join(root, 'profile', 'last-home.json'))).toBe(true)
  const again = await openApp(root, desktop.home)
  await expect(homeButton(again.page)).toHaveAttribute('data-home', 'work')
  // In the terminal, the active home is still the default one.
  expect(atlas(desktop.home, 'home', 'list')).toMatch(/^\* default/)
  await again.app.close()

  // Deleted in the terminal: the app opens the active home instead.
  atlas(desktop.home, 'home', 'delete', 'work', '--yes')
  const last = await openApp(root, desktop.home)
  await expect(homeButton(last.page)).toHaveAttribute('data-home', 'default')
  expect(await listedHomes(last.page)).toEqual(['default'])
  await last.app.close()
})

/** A PNG of one red pixel. */
const PIXEL = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

test('the window turns dark from Settings, and the home keeps a picture a backup carries', async () => {
  const { page } = desktop
  const dark = () => page.evaluate(() => document.documentElement.classList.contains('dark'))
  const before = await dark()

  // The look moves at once, and stays after the window reloads (step 15b: from the home menu to
  // Settings → General, with the system's as a third choice).
  const settings = await openSettings(page, 'general')
  await settings
    .getByTestId('setting-theme')
    .locator(`[data-value="${before ? 'light' : 'dark'}"]`)
    .click()
  expect(await dark()).toBe(!before)
  await expect(
    settings.getByTestId('setting-theme').locator(`[data-value="${before ? 'light' : 'dark'}"]`)
  ).toHaveAttribute('aria-checked', 'true')
  await page.keyboard.press('Escape')
  await page.reload()
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })
  await expect.poll(dark).toBe(!before)

  // A picture chosen in the home's dialog: a square of 256 pixels, kept in the home.
  const image = path.join(desktop.folder, 'me.png')
  writeFileSync(image, Buffer.from(PIXEL, 'base64'))
  const profile = await editHome(page)
  await profile.getByTestId('home-picture-input').setInputFiles(image)
  await expect(homeButton(page).getByTestId('home-picture-image')).toBeVisible()
  const kept = path.join(desktop.home, 'picture.png')
  await expect.poll(() => existsSync(kept)).toBe(true)
  const png = readFileSync(kept)
  expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([256, 256])

  // What is not an image is refused, and the dialog says so.
  const notes = path.join(desktop.folder, 'notes.txt')
  writeFileSync(notes, 'not a picture')
  await profile.getByTestId('home-picture-input').setInputFiles(notes)
  await expect(profile.getByRole('alert')).toContainText('not an image')

  // Taken away, from the home too.
  await profile.getByTestId('home-picture-remove').click()
  await expect(homeButton(page).getByTestId('home-picture-image')).toHaveCount(0)
  await expect.poll(() => existsSync(kept)).toBe(false)
})
