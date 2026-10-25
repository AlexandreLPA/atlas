// The site on Cloudflare: the built files, with this Worker in front of the pages. An agent that
// asks for Markdown (Accept: text/markdown) gets the page's Markdown twin; every page announces its
// twin, and the home what agents read first, in Link headers.
import { CONTENT_SIGNAL, pageLinks, twinPath, wantsMarkdown } from './routing'

interface Env {
  ASSETS: Fetcher
}

function markdown(response: Response, head: boolean): Response {
  const headers = new Headers(response.headers)
  headers.set('Content-Type', 'text/markdown; charset=utf-8')
  headers.set('Vary', 'Accept')
  headers.set('Content-Signal', CONTENT_SIGNAL)
  return new Response(head ? null : response.body, { status: response.status, headers })
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url)
    const reading = request.method === 'GET' || request.method === 'HEAD'
    if (reading && wantsMarkdown(request.headers.get('Accept'))) {
      const twin = twinPath(url.pathname)
      if (twin) {
        const answer = await env.ASSETS.fetch(new Request(new URL(twin, url), { method: request.method }))
        if (answer.ok) {
          return markdown(answer, request.method === 'HEAD')
        }
      }
    }

    const response = await env.ASSETS.fetch(request)
    if (url.pathname.endsWith('.md') && response.ok) {
      return markdown(response, request.method === 'HEAD')
    }
    if (!(response.headers.get('Content-Type') ?? '').startsWith('text/html')) {
      return response
    }
    const headers = new Headers(response.headers)
    headers.append('Vary', 'Accept')
    for (const link of pageLinks(url.pathname)) {
      headers.append('Link', link)
    }
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers })
  }
} satisfies ExportedHandler<Env>
