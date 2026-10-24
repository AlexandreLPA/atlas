/**
 * The previews' pages (step 16f), served by the main process.
 *
 * - **A page is served from a protocol of its own** (`atlas-preview:`), with a policy of its own
 *   that lets its scripts run, and shown in a frame sandboxed with no `allow-same-origin`, so its
 *   origin is opaque: it reaches neither the window, nor the vault, nor the server. The window's own
 *   policy forbids inline scripts, and a `srcdoc` frame inherits it (found by the e2e test on
 *   15/10/2026: the page's script never ran), so the page cannot be the frame's `srcdoc`. Its
 *   policy forbids every connection: the page may have
 *   come from a site the agent read. Scripts, styles, images and fonts may come from the web, as a
 *   page made with a library from a CDN needs.
 * - **A file is the server's** (step 15, `preview.read`): it is read where the server is, on this
 *   computer as on another machine, and only its page is served here.
 */
import crypto from 'node:crypto'

import type { IpcMain, Protocol } from 'electron'

/** The protocol a preview's page is served from. */
export const PREVIEW_SCHEME = 'atlas-preview'
/** The pages served at once, the oldest let go past it. */
const PAGES_KEPT = 50
/** What a page may do: run its scripts and load a library from the web, and connect nowhere. */
export const PAGE_POLICY =
  "default-src 'none'; script-src 'unsafe-inline' https:; style-src 'unsafe-inline' https:; " +
  'img-src data: https:; font-src data: https:; media-src data: https:'

/** The pages being served, by id. */
export class PreviewPages {
  private readonly pages = new Map<string, string>()

  /** Serve `html`; its address. */
  host(html: string): string {
    const id = crypto.randomUUID()
    this.pages.set(id, html)

    while (this.pages.size > PAGES_KEPT) {
      this.pages.delete(this.pages.keys().next().value ?? '')
    }

    return `${PREVIEW_SCHEME}://page/${id}`
  }

  /** The page at an address, with its policy; a 404 for one it does not hold. */
  respond(url: string): Response {
    const id = new URL(url).pathname.replace(/^\//, '')
    const html = this.pages.get(id)

    if (html === undefined) {
      return new Response('Not found.', { status: 404 })
    }

    return new Response(html, {
      headers: { 'content-security-policy': PAGE_POLICY, 'content-type': 'text/html; charset=utf-8' }
    })
  }
}

/** Serve the previews' pages. */
export function registerPreviews(ipc: IpcMain, protocol: Protocol): void {
  const pages = new PreviewPages()
  protocol.handle(PREVIEW_SCHEME, request => pages.respond(request.url))
  ipc.handle('atlas:preview:host', (_event, html: unknown) => pages.host(typeof html === 'string' ? html : ''))
}
