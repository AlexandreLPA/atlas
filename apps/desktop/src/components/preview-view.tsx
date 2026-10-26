import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'
import { type PluginConfig, Streamdown } from 'streamdown'

import { t } from '@/i18n'
import { $machine, readPreview } from '@/store/app'
import type { PreviewTarget } from '@/store/previews'

/** What a guest page gets: its scripts run, in an opaque origin with no reach into the app. */
export const DEFAULT_SANDBOX = 'allow-scripts'

/**
 * The sandbox tokens a frame may add. An allow list, not a block list: the attribute is a
 * case-insensitive token set, and a block list can let `ALLOW-SAME-ORIGIN` through. Never
 * `allow-same-origin` (the opaque origin is the containment), nor the navigation, popup and modal
 * family (a guest driving the app's window).
 */
const ALLOWED_TOKENS = new Set(['allow-forms', 'allow-scripts'])

/** Keep the allowed tokens of `sandbox`, lower-cased; none left is the default posture, never an
 * unsandboxed frame. */
export function sanitizeSandbox(sandbox: string | undefined): string {
  const kept = new Set<string>()

  for (const word of (sandbox ?? '').split(/\s+/)) {
    const token = word.trim().toLowerCase()

    if (ALLOWED_TOKENS.has(token)) {
      kept.add(token)
    }
  }

  return kept.size === 0 ? DEFAULT_SANDBOX : [...kept].join(' ')
}

/** Only a web page is framed by address: `file:` would read the disk into a scripted frame. */
export function frameableUrl(url: string): boolean {
  try {
    return ['http:', 'https:'].includes(new URL(url).protocol)
  } catch {
    return false
  }
}

/**
 * A preview tab's page (step 16f): an artifact or a file's HTML in the isolated frame, a local site
 * by its address in the same frame, Markdown and mermaid read as an answer reads them, an image,
 * or a file's text. The HTML may have come from a page the agent read: in the frame its scripts
 * run, and reach neither the app, nor the vault, nor the server.
 */
export function PreviewView({ target }: { target: PreviewTarget }) {
  if (target.kind === 'url') {
    return target.url && frameableUrl(target.url) ? (
      <SiteView title={target.label} url={target.url} />
    ) : (
      <Note text={t.preview.notWeb} />
    )
  }

  if (target.kind === 'file') {
    return <FileView path={target.path ?? ''} title={target.label} />
  }

  return <Content language={target.language ?? 'markdown'} text={target.content ?? ''} title={target.label} />
}

/** A site by its address; one on another machine's loopback reached through its SSH tunnel, or a
 * note that it is that machine's (step 15). */
function SiteView({ title, url }: { title: string; url: string }) {
  const machine = useStore($machine)
  const [reached, setReached] = useState<null | { error: string } | { url: string }>(machine === null ? { url } : null)

  useEffect(() => {
    if (machine === null) {
      setReached({ url })

      return
    }

    let current = true
    setReached(null)
    void window.atlas.previewReach(machine, url).then(found => {
      if (current) {
        setReached(found)
      }
    })

    return () => {
      current = false
    }
  }, [machine, url])

  if (!reached) {
    return <Note text={t.preview.reading} />
  }

  return 'url' in reached ? <Frame src={reached.url} title={title} /> : <Note text={reached.error} />
}

function FileView({ path, title }: { path: string; title: string }) {
  const [file, setFile] = useState<null | PreviewFile>(null)

  useEffect(() => {
    let current = true
    void readPreview(path).then(read => {
      if (current) {
        setFile(read)
      }
    })

    return () => {
      current = false
    }
  }, [path])

  if (!file) {
    return <Note text={t.preview.reading} />
  }

  switch (file.kind) {
    case 'error':
      return <Note text={file.error} />
    case 'image':
      return (
        <div className="grid h-full place-items-center overflow-auto bg-white p-4">
          <img alt={title} className="max-h-full max-w-full" src={file.dataUrl} />
        </div>
      )
    case 'html':
      return <Content language="html" text={file.text} title={title} />
    case 'markdown':
      return <Content language="markdown" text={file.text} title={title} />
    case 'text':
      return <pre className="h-full overflow-auto bg-white p-3 font-mono text-xs text-foreground">{file.text}</pre>
  }
}

function Content({ language, text, title }: { language: string; text: string; title: string }) {
  const [plugins, setPlugins] = useState<PluginConfig | undefined>(undefined)

  // Mermaid is heavy: loaded when a diagram is shown, in its strict mode, which cleans the
  // diagram's text before drawing it.
  useEffect(() => {
    if (language !== 'mermaid') {
      return
    }

    let current = true
    void import('mermaid').then(({ default: mermaid }) => {
      if (current) {
        setPlugins({
          mermaid: {
            getMermaid: config => {
              mermaid.initialize({ ...config, securityLevel: 'strict', startOnLoad: false })

              return mermaid
            },
            language: 'mermaid',
            name: 'mermaid',
            type: 'diagram'
          }
        })
      }
    })

    return () => {
      current = false
    }
  }, [language])

  if (language === 'html') {
    return <Frame html={text} title={title} />
  }

  if (language === 'mermaid' && !plugins) {
    return <Note text={t.preview.reading} />
  }

  const fence = '```'
  const markdown = language === 'mermaid' ? `${fence}mermaid\n${text}\n${fence}` : text

  return (
    <div className="h-full overflow-auto bg-white p-4 text-foreground" data-testid="preview-markdown">
      <Streamdown mode="static" plugins={plugins}>
        {markdown}
      </Streamdown>
    </div>
  )
}

/** The isolated frame: a page's HTML, served by the app from its own protocol with its own policy
 * (electron/preview-ipc.ts), or a local site by its address. Kept light: a
 * generated page assumes a light canvas. */
function Frame({ html, src, title }: { html?: string; src?: string; title: string }) {
  const [served, setServed] = useState<null | string>(null)

  useEffect(() => {
    if (html === undefined) {
      return
    }

    let current = true
    void window.atlas.previewPage(html).then(url => {
      if (current) {
        setServed(url)
      }
    })

    return () => {
      current = false
    }
  }, [html])

  const address = html === undefined ? src : served

  if (!address) {
    return <Note text={t.preview.reading} />
  }

  return (
    <iframe
      className="block size-full border-0 bg-white"
      data-testid="preview-frame"
      referrerPolicy="no-referrer"
      sandbox={sanitizeSandbox(html === undefined ? 'allow-scripts allow-forms' : DEFAULT_SANDBOX)}
      src={address}
      style={{ colorScheme: 'light' }}
      title={title}
    />
  )
}

function Note({ text }: { text: string }) {
  return <p className="p-3 text-xs text-muted-foreground">{text}</p>
}
