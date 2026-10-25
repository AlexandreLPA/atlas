// `npm run icons`: draw the app's icon files from resources/icon.svg, after a change to it. icon.png
// is the window's on Linux and the Dock's on macOS; icon.ico is the window's, the taskbar's and the
// Start Menu's on Windows. The small sizes draw the mark's lines bolder, or they would fade below a
// pixel. Electron draws them, as it draws the app.
/* global document, Image */
import { readFileSync, writeFileSync } from 'node:fs'

import { app, BrowserWindow } from 'electron'

const RESOURCES = new URL('../resources/', import.meta.url)

/** Each size the .ico holds, with how much wider #mark's stroke is drawn there, in its own units. */
const SIZES = [
  [16, 32],
  [20, 28],
  [24, 26],
  [32, 20],
  [40, 16],
  [48, 12],
  [64, 8],
  [128, 2],
  [256, 0]
]
/** The PNG's side: the Dock's largest tile, and plenty for Linux's app grid. */
const PNG_SIDE = 512
/** From this side up an image in the .ico is a PNG; below, a bitmap, which every reader of icons knows. */
const PNG_FROM = 256

/** The mark's path in the SVG, with the stroke the small sizes widen. */
const MARK = /(<path id="mark"[^>]*? stroke-width=")0(")/

/** The SVG with #mark's stroke `bold` units wide. */
function bolder(svg, bold) {
  if (!MARK.test(svg)) {
    throw new Error('resources/icon.svg needs its <path id="mark"> with stroke-width="0", which the small sizes widen.')
  }

  return svg.replace(MARK, `$1${bold}$2`)
}

/** Run in the window: the SVG drawn at `side` pixels, as a PNG's data URL and as its RGBA pixels. */
async function draw(svg, side) {
  const image = new Image()
  await new Promise((resolve, reject) => {
    image.onload = resolve
    image.onerror = reject
    image.src = `data:image/svg+xml;base64,${btoa(svg)}`
  })
  const canvas = document.createElement('canvas')
  canvas.width = side
  canvas.height = side
  const context = canvas.getContext('2d')
  context.drawImage(image, 0, 0, side, side)

  return { pixels: Array.from(context.getImageData(0, 0, side, side).data), png: canvas.toDataURL('image/png') }
}

/** A 32-bit bitmap as an .ico holds it: its header, the rows bottom-up in BGRA, then the mask of the
 * clear pixels, which is why the header says twice the height. */
function bitmap(side, pixels) {
  const maskRow = Math.ceil(side / 32) * 4
  const header = Buffer.alloc(40)
  header.writeUInt32LE(40, 0)
  header.writeInt32LE(side, 4)
  header.writeInt32LE(side * 2, 8)
  header.writeUInt16LE(1, 12)
  header.writeUInt16LE(32, 14)
  header.writeUInt32LE(side * side * 4 + maskRow * side, 20)
  const colors = Buffer.alloc(side * side * 4)
  const mask = Buffer.alloc(maskRow * side)

  for (let y = 0; y < side; y++) {
    const row = side - 1 - y

    for (let x = 0; x < side; x++) {
      const from = (y * side + x) * 4
      const to = (row * side + x) * 4
      colors[to] = pixels[from + 2]
      colors[to + 1] = pixels[from + 1]
      colors[to + 2] = pixels[from]
      colors[to + 3] = pixels[from + 3]

      if (pixels[from + 3] === 0) {
        mask[row * maskRow + (x >> 3)] |= 0x80 >> (x & 7)
      }
    }
  }

  return Buffer.concat([header, colors, mask])
}

/** The .ico: its header, one entry per image (a side of 256 is written 0), then the images. */
function icon(images) {
  const head = Buffer.alloc(6 + 16 * images.length)
  head.writeUInt16LE(1, 2)
  head.writeUInt16LE(images.length, 4)
  let offset = head.length

  images.forEach(({ data, side }, index) => {
    const entry = 6 + 16 * index
    head.writeUInt8(side % 256, entry)
    head.writeUInt8(side % 256, entry + 1)
    head.writeUInt16LE(1, entry + 4)
    head.writeUInt16LE(32, entry + 6)
    head.writeUInt32LE(data.length, entry + 8)
    head.writeUInt32LE(offset, entry + 12)
    offset += data.length
  })

  return Buffer.concat([head, ...images.map(({ data }) => data)])
}

function fromDataUrl(url) {
  return Buffer.from(url.slice(url.indexOf(',') + 1), 'base64')
}

/** Every file, drawn in a window that is never shown. */
async function make() {
  const svg = readFileSync(new URL('icon.svg', RESOURCES), 'utf8')
  const window = new BrowserWindow({ show: false, webPreferences: { backgroundThrottling: false } })
  await window.loadURL('about:blank')
  const drawn = (side, bold) =>
    window.webContents.executeJavaScript(`(${draw.toString()})(${JSON.stringify(bolder(svg, bold))}, ${side})`)
  const images = []

  for (const [side, bold] of SIZES) {
    const { pixels, png } = await drawn(side, bold)
    images.push({ data: side >= PNG_FROM ? fromDataUrl(png) : bitmap(side, pixels), side })
  }

  writeFileSync(new URL('icon.ico', RESOURCES), icon(images))
  writeFileSync(new URL('icon.png', RESOURCES), fromDataUrl((await drawn(PNG_SIDE, 0)).png))
}

// Not awaited at the top: Electron is ready only once this module has run.
// A failure is said and ends the run; left alone, Electron would hold it open on a dialog.
void app
  .whenReady()
  .then(make)
  .then(
    () => {
      app.quit()
    },
    error => {
      console.error(error)
      app.exit(1)
    }
  )
