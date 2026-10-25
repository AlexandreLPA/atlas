// How the Worker in front of the site answers: which requests want Markdown, where each page's
// Markdown twin is, and which links a page announces in its headers. The build reads the content
// signal from here too, so robots.txt and the Worker's answers say the same.

/**
 * How the site lets its pages be used. Atlas is open source and wants to be known, models
 * included: search, answers and training are all allowed. To decide again before publishing if
 * the project chooses otherwise (ROADMAP, step 19).
 */
export const CONTENT_SIGNAL = 'search=yes, ai-input=yes, ai-train=yes'

/** Whether a request's Accept header prefers Markdown to HTML. Browsers never ask for Markdown. */
export function wantsMarkdown(accept: string | null): boolean {
  if (!accept) {
    return false
  }
  let markdown = -1
  let html = -1
  for (const range of accept.split(',')) {
    const [type = '', ...params] = range.split(';').map(part => part.trim())
    const q = params.find(param => param.startsWith('q='))
    const quality = q ? Number(q.slice(2)) : 1
    const name = type.toLowerCase()
    if (name === 'text/markdown' || name === 'text/x-markdown') {
      markdown = Math.max(markdown, quality)
    } else if (name === 'text/html') {
      html = Math.max(html, quality)
    }
  }
  return markdown > 0 && markdown >= html
}

/** The Markdown twin of a page: /docs/install/ is /docs/install.md, the home is /index.md. */
export function twinPath(pathname: string): string | null {
  if (pathname.endsWith('.md')) {
    return pathname
  }
  const last = pathname.split('/').pop() ?? ''
  if (last.includes('.') && !last.endsWith('.html')) {
    return null
  }
  const path = pathname
    .replace(/\/index\.html$/, '')
    .replace(/\.html$/, '')
    .replace(/\/+$/, '')
  return path ? `${path}.md` : '/index.md'
}

/** The Link header values of a page: its twin, and on the home what agents look for first. */
export function pageLinks(pathname: string): string[] {
  const links: string[] = []
  const twin = twinPath(pathname)
  if (twin) {
    links.push(`<${twin}>; rel="alternate"; type="text/markdown"`)
  }
  if (pathname === '/' || pathname === '/index.html') {
    links.push(
      '</llms.txt>; rel="describedby"; type="text/plain"',
      '</docs/>; rel="service-doc"; type="text/html"',
      '</sitemap-index.xml>; rel="sitemap"; type="application/xml"',
      '</.well-known/agent-skills/index.json>; rel="describedby"; type="application/json"'
    )
  }
  return links
}
