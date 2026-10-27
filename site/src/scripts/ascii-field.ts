// The site's field of characters, in two places. The wordmark in the footer is the word drawn on a
// hidden canvas, read back cell by cell and set in characters: the cells inside the letters are
// bright and keep changing. The background behind the top of each page is the same field with no word,
// fading out below the first screen, so only a faint grain shows until the pointer passes over it. Without motion, or without JavaScript, the
// word is still there as the plain heading it replaces and the background is a plain dark page.
const CHARS = '01{}[]<>/|=+*#%&$?:;.,~^'
// The letters themselves are set in the densest characters, so the word reads at a glance.
const DENSE = '#%&$@8B0WM*+='
const FRAME = 1000 / 24
/** How long, in milliseconds, a word takes to assemble. */
const ASSEMBLE = 1400

interface Field {
  canvas: HTMLCanvasElement
  /** The element the pointer is tracked over, and that sets the canvas's width. */
  area: HTMLElement | Window
  cellW: number
  cellH: number
  reach: number
  word?: string
  /** The height of a wordmark as a share of its width, and where its word sits. */
  ratio?: number
  align?: 'start' | 'center'
  /** The grain's brightness outside the letters, as a floor and a spread. */
  grain: [number, number]
}

const still = window.matchMedia('(prefers-reduced-motion: reduce)')

function mount(field: Field) {
  const { canvas, area, cellW, cellH, reach, word, grain, ratio: shape = 0.26, align = 'center' } = field
  const context = canvas.getContext('2d')
  if (!context) {
    return
  }
  const pointer = { x: -1e4, y: -1e4 }
  let cols = 0
  let rows = 0
  let mask = new Uint8Array(0)
  let seeds = new Float32Array(0)
  let frame = 0
  let last = 0
  let born = -1
  let visible = true

  const size = () =>
    area instanceof Window
      ? { width: document.documentElement.clientWidth, height: Math.min(Math.round(area.innerHeight * 1.1), 980) }
      : { width: area.clientWidth, height: Math.max(Math.floor(area.clientWidth * shape), cellH * 8) }

  const measure = () => {
    const ratio = Math.min(window.devicePixelRatio || 1, 2)
    const { width, height } = size()
    cols = Math.ceil(width / cellW)
    rows = Math.ceil(height / cellH)
    canvas.width = cols * cellW * ratio
    canvas.height = rows * cellH * ratio
    canvas.style.height = `${rows * cellH}px`
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    mask = new Uint8Array(cols * rows)
    seeds = Float32Array.from({ length: cols * rows }, () => Math.random())
    if (word) {
      drawWord()
    }
  }

  const drawWord = () => {
    const sample = document.createElement('canvas')
    sample.width = cols
    sample.height = rows
    const draw = sample.getContext('2d', { willReadFrequently: true })
    if (!draw) {
      return
    }
    // A cell is taller than it is wide, so the word is drawn stretched sideways to look natural.
    const stretch = cellH / cellW
    const face = (px: number) => `800 ${px}px "Mona Sans Variable", system-ui, sans-serif`
    draw.textAlign = align === 'start' ? 'left' : 'center'
    draw.textBaseline = 'middle'
    let px = rows * 0.88
    draw.font = face(px)
    const wide = draw.measureText(word ?? '').width * stretch
    px = Math.floor(px * Math.min((cols * 0.97) / wide, 1))
    draw.font = face(px)
    draw.scale(stretch, 1)
    draw.fillStyle = '#fff'
    draw.fillText(word ?? '', align === 'start' ? 0 : cols / stretch / 2, rows / 2 + px * 0.04)
    const pixels = draw.getImageData(0, 0, cols, rows).data
    for (let i = 0; i < mask.length; i++) {
      mask[i] = (pixels[i * 4 + 3] ?? 0) > 110 ? 1 : 0
    }
  }

  const render = (time: number) => {
    const light = document.documentElement.dataset.theme === 'light'
    context.clearRect(0, 0, canvas.width, canvas.height)
    context.font = `${light ? 700 : 500} ${cellH - 2}px "Martian Mono Variable", ui-monospace, monospace`
    context.textBaseline = 'top'
    const tick = still.matches ? 0 : Math.floor(time / 140)
    const ink = getComputedStyle(canvas).getPropertyValue('--fg-rgb').trim() || '255 255 255'
    const accent = getComputedStyle(canvas).getPropertyValue('--brass').trim() || '#ff5a1f'
    // The word assembles from left to right when it first shows, then a band of light sweeps
    // across it every few seconds. With reduced motion it is simply there.
    const elapsed = still.matches ? Infinity : born < 0 ? 0 : time - born
    const built = elapsed / ASSEMBLE
    const sweep = still.matches ? -1e4 : ((time / 16) % (cols + 120)) - 60
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const i = y * cols + x
        const seed = seeds[i] ?? 0
        const inside = mask[i] === 1 && (word === undefined || x / cols + seed * 0.12 < built)
        const lit = mask[i] === 1 && Math.abs(x + y * 0.6 - sweep) < 9
        const away = Math.hypot(x * cellW - pointer.x, y * cellH - pointer.y)
        const glow = away < reach ? (1 - away / reach) ** 2 : 0
        const churn = (tick + Math.floor(seed * 97)) % (inside ? 5 : 23) === 0
        const set = inside ? DENSE : CHARS
        const char = set.charAt(Math.floor((seed * 1000 + (churn || glow > 0.05 ? tick : 0)) % set.length))
        const base = inside ? (light ? 1 : 0.82 + seed * 0.18) : (grain[0] + seed * grain[1]) * (light ? 0.7 : 1)
        const alpha = Math.min(1, base + glow * 0.7)
        context.fillStyle = glow > 0.25 || lit ? accent : `rgb(${ink} / ${alpha})`
        context.globalAlpha = glow > 0.25 || lit ? alpha : 1
        context.fillText(char, x * cellW, y * cellH)
      }
    }
  }

  const loop = (time: number) => {
    if (time - last >= FRAME) {
      last = time
      render(time)
    }
    frame = visible && !still.matches ? requestAnimationFrame(loop) : 0
  }

  const start = () => {
    if (!frame) {
      frame = requestAnimationFrame(loop)
    }
  }

  measure()
  render(0)
  canvas.dataset.ready = ''
  if (area instanceof Window) {
    window.addEventListener('resize', () => {
      measure()
      render(0)
      start()
    })
    window.addEventListener('pointermove', event => {
      pointer.x = event.pageX
      pointer.y = event.pageY
    })
    document.addEventListener('pointerleave', () => {
      pointer.x = -1e4
      pointer.y = -1e4
    })
  } else {
    new ResizeObserver(() => {
      measure()
      render(0)
    }).observe(area)
    new IntersectionObserver(entries => {
      visible = entries.some(entry => entry.isIntersecting)
      if (visible && born < 0) {
        born = performance.now()
      }
      if (visible) {
        start()
      }
    }).observe(area)
    area.addEventListener('pointermove', event => {
      const box = area.getBoundingClientRect()
      pointer.x = event.clientX - box.left
      pointer.y = event.clientY - box.top
    })
    area.addEventListener('pointerleave', () => {
      pointer.x = -1e4
      pointer.y = -1e4
    })
  }
  still.addEventListener('change', () => {
    render(0)
    start()
  })
  start()
}

for (const wordmark of document.querySelectorAll<HTMLElement>('[data-wordmark]')) {
  const canvas = wordmark.querySelector('canvas')
  if (canvas) {
    mount({
      canvas,
      area: wordmark,
      cellW: Number(wordmark.dataset.cellw ?? 7),
      cellH: Number(wordmark.dataset.cellh ?? 12),
      reach: 90,
      word: wordmark.dataset.wordmark ?? 'Atlas',
      ratio: Number(wordmark.dataset.ratio ?? 0.26),
      align: wordmark.dataset.align === 'start' ? 'start' : 'center',
      grain: [0.03, 0.06]
    })
  }
}
