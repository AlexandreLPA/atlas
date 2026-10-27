// Headlines resolve out of the site's own characters: each letter cycles through random ones, in
// brass, and settles left to right. The words keep their line breaks while they change, the page
// reads the real headline throughout, and with reduced motion the headline is simply there.
const GLYPHS = 'abcdefghijklmnopqrstuvwxyz0123456789{}[]<>/=+*#%&$'
const STEP = 38
const SETTLE = 26

const glyph = (like: string) => {
  const pick = GLYPHS.charAt(Math.floor(Math.random() * GLYPHS.length))
  return like === like.toUpperCase() && like !== like.toLowerCase() ? pick.toUpperCase() : pick
}

function scramble(heading: HTMLElement) {
  const text = heading.textContent.trim()
  if (!text) {
    return
  }
  heading.setAttribute('aria-label', text)
  heading.textContent = ''
  const cells: { span: HTMLSpanElement; char: string }[] = []
  const words = text.split(/(\s+)/)
  for (const part of words) {
    if (/^\s+$/.test(part)) {
      heading.append(' ')
      continue
    }
    const word = document.createElement('span')
    word.setAttribute('aria-hidden', 'true')
    word.style.whiteSpace = 'nowrap'
    for (const char of part) {
      const span = document.createElement('span')
      span.textContent = char
      word.append(span)
      cells.push({ span, char })
    }
    heading.append(word)
  }
  const start = performance.now()
  const frame = (now: number) => {
    const settled = (now - start) / STEP
    let open = false
    for (const [index, { span, char }] of cells.entries()) {
      if (index <= settled - SETTLE * 0.25 + (index % 3)) {
        if (span.dataset.done !== '') {
          span.textContent = char
          span.style.color = ''
          span.dataset.done = ''
        }
      } else if (index <= settled + SETTLE) {
        open = true
        span.textContent = glyph(char)
        span.style.color = 'var(--brass)'
      } else {
        open = true
        span.textContent = ' '
      }
    }
    if (open) {
      requestAnimationFrame(frame)
    }
  }
  requestAnimationFrame(frame)
}

if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const headings = Array.from(document.querySelectorAll<HTMLElement>('main h1, main h2')).filter(
    heading => !heading.closest('[hidden]') && !heading.querySelector('*')
  )
  const observer = new IntersectionObserver(
    entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          observer.unobserve(entry.target)
          scramble(entry.target as HTMLElement)
        }
      }
    },
    { threshold: 0.6 }
  )
  for (const heading of headings) {
    observer.observe(heading)
  }
}
