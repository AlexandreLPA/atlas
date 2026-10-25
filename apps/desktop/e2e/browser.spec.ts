import http from 'node:http'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, openHomeMenu } from './harness'

let desktop: Desktop | undefined
let site: http.Server | undefined
const hits: string[] = []

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
  site?.close()
  site = undefined
  hits.length = 0
})

const PAGES: Record<string, string> = {
  '/': '<title>Home page</title><p>home</p>',
  '/two': '<title>Second page</title><p>two</p>',
  '/pop': '<title>Opener</title><button id="go" onclick="window.open(\'/popped\')">open</button>',
  '/popped': '<title>Popped page</title><p>popped</p>'
}

/** A site on this machine, which says what was asked of it. */
async function serve(): Promise<string> {
  site = http.createServer((request, response) => {
    const url = request.url ?? '/'
    hits.push(url)
    response.setHeader('content-type', 'text/html')
    response.end(PAGES[url] ?? '<title>Not found</title>')
  })
  await new Promise<void>(resolve => site?.listen(0, '127.0.0.1', resolve))

  return `http://127.0.0.1:${String((site.address() as { port: number }).port)}`
}

/** The native views laid over the window: what the page of the app cannot see. */
function views(app: Desktop['app']) {
  return app.evaluate(({ BrowserWindow }) =>
    (BrowserWindow.getAllWindows()[0]?.contentView.children ?? []).map(view => ({
      bounds: view.getBounds(),
      visible: view.getVisible()
    }))
  )
}

const shown = async (app: Desktop['app']) => (await views(app)).filter(view => view.visible)

test('the Browser tab opens a page, goes back, forward and reload, and refuses what is no web page', async () => {
  const base = await serve()
  desktop = await launch()
  const { app, page } = desktop
  await page.setViewportSize({ height: 820, width: 1200 })

  await page.getByTestId('panel-show').click()
  await page.getByTestId('browser-tab').click()
  await expect(page.getByTestId('browser-view')).toBeVisible()
  await expect(page.getByTestId('browser-page')).toHaveCount(0)

  const address = page.getByTestId('browser-address')
  // A bare host and port is this machine's, so it gets http.
  await address.fill(base.replace('http://', ''))
  await address.press('Enter')
  await expect(page.getByTestId('browser-page')).toHaveText(/Home page/)
  await expect(address).toHaveValue(`${base}/`)
  await expect.poll(async () => (await shown(app)).length).toBe(1)
  const [view] = await shown(app)
  const box = await page.getByTestId('browser-box').boundingBox()
  // The view lies exactly where the panel's box is.
  expect(Math.abs((view?.bounds.x ?? 0) - (box?.x ?? 0))).toBeLessThanOrEqual(1)
  expect(Math.abs((view?.bounds.width ?? 0) - (box?.width ?? 0))).toBeLessThanOrEqual(1)
  expect(view?.bounds.height).toBeGreaterThan(200)

  await address.fill(`${base}/two`)
  await address.press('Enter')
  await expect(page.getByTestId('browser-page')).toHaveText(/Second page/)
  await expect(page.getByTestId('browser-back')).toBeEnabled()
  await expect(page.getByTestId('browser-forward')).toBeDisabled()

  await page.getByTestId('browser-back').click()
  await expect(page.getByTestId('browser-page')).toHaveText(/Home page/)
  await expect(page.getByTestId('browser-forward')).toBeEnabled()
  await page.getByTestId('browser-forward').click()
  await expect(page.getByTestId('browser-page')).toHaveText(/Second page/)

  const before = hits.length
  await page.getByTestId('browser-reload').click()
  await expect.poll(() => hits.length).toBeGreaterThan(before)

  // No web address, no page of another scheme: refused, nothing loaded, nothing asked of the site.
  const asked = hits.length
  for (const typed of ['hello world', 'file:///C:/Windows/win.ini', 'javascript:alert(1)']) {
    await address.fill(typed)
    await address.press('Enter')
    await expect(page.getByRole('alert')).toBeVisible()
  }

  expect(hits.length).toBe(asked)
  expect(await shown(app)).toHaveLength(1)
})

test('a pop-up becomes a page of the browser; the view follows the panel and goes with it', async () => {
  const base = await serve()
  desktop = await launch()
  const { app, page } = desktop
  await page.setViewportSize({ height: 820, width: 1200 })
  await page.getByTestId('panel-show').click()
  await page.getByTestId('browser-tab').click()
  const address = page.getByTestId('browser-address')
  await address.fill(`${base}/pop`)
  await address.press('Enter')
  await expect(page.getByTestId('browser-page')).toHaveText(/Opener/)

  // A click in the page, with a gesture, as a user's would be.
  await app.evaluate(async ({ BrowserWindow }) => {
    const [window] = BrowserWindow.getAllWindows()
    const contents = window?.contentView.children
      .map(view => (view as unknown as { webContents?: Electron.WebContents }).webContents)
      .find(each => each?.getURL().endsWith('/pop'))
    await contents?.executeJavaScript('document.getElementById("go").click()', true)
  })
  await expect(page.getByTestId('browser-page')).toHaveCount(2)
  await expect(page.getByTestId('browser-page').nth(1)).toHaveText(/Popped page/)
  await expect(address).toHaveValue(`${base}/popped`)
  // One view shows, the pop-up's; the opener's is hidden behind it.
  expect(await shown(app)).toHaveLength(1)
  await page.getByTestId('browser-page').first().getByRole('button').first().click()
  await expect(address).toHaveValue(`${base}/pop`)

  // It follows the panel's size: the window's height is the panel's.
  const tall = (await shown(app))[0]?.bounds.height ?? 0
  await page.setViewportSize({ height: 640, width: 1200 })
  await expect.poll(async () => (await shown(app))[0]?.bounds.height ?? 0).toBeLessThan(tall - 100)

  // A dialog of the app lies over the panel: the view steps aside, and comes back with it closed.
  await openHomeMenu(page)
  await expect.poll(async () => (await shown(app)).length).toBe(0)
  await page.keyboard.press('Escape')
  await expect.poll(async () => (await shown(app)).length).toBe(1)

  // Another tab of the panel, or the panel hidden: no view.
  await page.getByTestId('panel-hide').click()
  await expect.poll(async () => (await shown(app)).length).toBe(0)
  await page.getByTestId('panel-show').click()
  await expect.poll(async () => (await shown(app)).length).toBe(1)

  // Closing the pages leaves no view behind.
  await page.getByTestId('browser-page').last().getByLabel('Close this page').click()
  await page.getByTestId('browser-page').last().getByLabel('Close this page').click()
  await expect(page.getByTestId('browser-page')).toHaveCount(0)
  expect(await views(app)).toHaveLength(0)
})
