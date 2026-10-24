import { describe, expect, it } from 'vitest'

import { PAGE_POLICY, PREVIEW_SCHEME, PreviewPages } from './preview-ipc'

describe("a preview's page, served with its own policy (step 16f)", () => {
  it('runs its scripts and connects nowhere, and only the pages it holds are served', async () => {
    const pages = new PreviewPages()
    const url = pages.host('<p>Hi</p>')

    const served = pages.respond(url)
    const unknown = pages.respond(`${PREVIEW_SCHEME}://page/nope`)

    expect(url.startsWith(`${PREVIEW_SCHEME}://page/`)).toBe(true)
    expect(await served.text()).toBe('<p>Hi</p>')
    expect(served.headers.get('content-security-policy')).toBe(PAGE_POLICY)
    expect(PAGE_POLICY).toContain("default-src 'none'")
    expect(PAGE_POLICY).not.toContain('connect-src')
    expect(unknown.status).toBe(404)
  })
})
