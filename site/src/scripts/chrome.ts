// The page's own frame: a line at the top that fills as the page is read, and the sections that
// band of names that runs faster the faster the page is scrolled. Both stop with reduced motion.
if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const bar = document.createElement('div')
  bar.className = 'progress'
  bar.setAttribute('aria-hidden', 'true')
  document.body.prepend(bar)
  let queued = false
  const update = () => {
    queued = false
    const room = document.documentElement.scrollHeight - window.innerHeight
    bar.style.transform = `scaleX(${room > 0 ? Math.min(window.scrollY / room, 1) : 0})`
  }
  window.addEventListener(
    'scroll',
    () => {
      if (!queued) {
        queued = true
        requestAnimationFrame(update)
      }
    },
    { passive: true }
  )
  update()

  const track = document.querySelector<HTMLElement>('.marquee-track')
  if (track) {
    track.style.animation = 'none'
    let offset = 0
    let last = window.scrollY
    const run = () => {
      const delta = Math.abs(window.scrollY - last)
      last = window.scrollY
      offset = (offset + 0.6 + Math.min(delta, 60) * 0.5) % (track.scrollWidth / 2)
      track.style.transform = `translateX(${-offset}px)`
      requestAnimationFrame(run)
    }
    requestAnimationFrame(run)
  }
}
