import { useStore } from '@nanostores/react'
import { type SyntheticEvent, useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import { $browser, browserCommand, followBrowser } from '@/store/browser'

import { ArrowLeft, ArrowRight, Close, Plus, Refresh, Stop, Web } from './ui/icons'
import { IconButton } from './ui/tip'

/** What lies over the panel, which a native view would cover: a dialog or a menu. A tooltip does not
 * count: it opens above the toolbar. */
const OVERLAY = '[role="alertdialog"], [role="dialog"], [role="menu"]'

/** Whether a dialog or a menu of the app is open, followed as the page's popups come and go (Radix
 * puts them in portals, directly under `body`). */
function useObscured(): boolean {
  const [obscured, setObscured] = useState(false)

  useEffect(() => {
    const look = () => {
      setObscured(document.body.querySelector(OVERLAY) !== null)
    }
    look()
    const observer = new MutationObserver(look)
    observer.observe(document.body, { childList: true })

    return () => {
      observer.disconnect()
    }
  }, [])

  return obscured
}

/** A page's name on its tab: its title, else its address's host, else the new page's. */
function tabName(tab: BrowserTab): string {
  if (tab.title) {
    return tab.title
  }

  try {
    return new URL(tab.url).host || t.browser.untitled
  } catch {
    return t.browser.untitled
  }
}

/**
 * The Browser tab of the side panel (docs/GLOSSARY.md, `app browser`): an address bar with back,
 * forward and reload, a strip of pages, and a box where the page's native view is laid, which the
 * main process places from the box's rectangle in the window, reported as the box moves or
 * resizes. While the box is not on screen, or a dialog or a menu of the app lies over it, the view
 * is hidden: a native view is always above the app's own page.
 */
export function BrowserView() {
  const { selected, tabs } = useStore($browser)
  const tab = tabs.find(each => each.id === selected)
  const box = useRef<HTMLDivElement>(null)
  const address = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState<null | string>(null)
  const [invalid, setInvalid] = useState(false)
  const obscured = useObscured()
  const hasPage = tab !== undefined

  useEffect(() => followBrowser(), [])

  useEffect(() => {
    const element = box.current

    if (!element || obscured || !hasPage) {
      return
    }

    const report = () => {
      const { height, width, x, y } = element.getBoundingClientRect()
      void browserCommand({ command: 'show', rect: { height, width, x, y } })
    }
    report()
    const observer = new ResizeObserver(report)
    observer.observe(element)
    observer.observe(document.documentElement)
    window.addEventListener('resize', report)

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', report)
      void browserCommand({ command: 'show', rect: null })
    }
  }, [obscured, hasPage])

  const submit = (event: SyntheticEvent) => {
    event.preventDefault()
    void browserCommand({ command: 'go', id: tab?.id ?? null, text: draft ?? tab?.url ?? '' }).then(answer => {
      if (answer && !answer.ok && answer.reason === 'address') {
        setInvalid(true)

        return
      }

      setInvalid(false)
      setDraft(null)
      address.current?.blur()
    })
  }

  return (
    <div className="absolute inset-0 flex flex-col bg-background" data-testid="browser-view">
      <form className="flex items-center gap-0.5 border-b border-border px-1.5 py-1" onSubmit={submit}>
        <IconButton
          disabled={!tab?.canGoBack}
          icon={ArrowLeft}
          label={t.browser.back}
          onClick={() => {
            if (tab) {
              void browserCommand({ command: 'back', id: tab.id })
            }
          }}
          testId="browser-back"
        />
        <IconButton
          disabled={!tab?.canGoForward}
          icon={ArrowRight}
          label={t.browser.forward}
          onClick={() => {
            if (tab) {
              void browserCommand({ command: 'forward', id: tab.id })
            }
          }}
          testId="browser-forward"
        />
        <IconButton
          disabled={!tab}
          icon={tab?.loading ? Stop : Refresh}
          label={tab?.loading ? t.browser.stop : t.browser.reload}
          onClick={() => {
            if (tab) {
              void browserCommand({ command: tab.loading ? 'stop' : 'reload', id: tab.id })
            }
          }}
          testId="browser-reload"
        />
        <input
          aria-invalid={invalid}
          aria-label={t.browser.address}
          autoComplete="off"
          className={`min-w-0 flex-1 rounded-md border bg-muted px-2 py-1 text-xs outline-none focus:bg-background ${
            invalid ? 'border-failed' : 'border-transparent focus:border-ring'
          }`}
          data-testid="browser-address"
          onBlur={() => {
            setDraft(null)
          }}
          onChange={event => {
            setInvalid(false)
            setDraft(event.target.value)
          }}
          onFocus={event => {
            event.target.select()
          }}
          placeholder={t.browser.placeholder}
          ref={address}
          spellCheck={false}
          type="text"
          value={draft ?? tab?.url ?? ''}
        />
      </form>
      {tabs.length > 0 && (
        <div className="flex items-center gap-1 overflow-x-auto border-b border-border px-1.5 py-1 text-xs">
          {tabs.map(each => (
            <div
              aria-selected={each.id === selected}
              className={`flex max-w-40 shrink-0 items-center gap-1 rounded px-2 py-0.5 ${
                each.id === selected ? 'bg-muted' : 'text-muted-foreground'
              }`}
              data-testid="browser-page"
              key={each.id}
              role="tab"
            >
              <button
                className="flex min-w-0 items-center gap-1"
                onClick={() => {
                  void browserCommand({ command: 'select', id: each.id })
                }}
                title={each.url}
                type="button"
              >
                <Web aria-hidden className="shrink-0" size={12} stroke={1.75} />
                <span className="truncate">{tabName(each)}</span>
              </button>
              <button
                aria-label={t.browser.closeTab}
                className="text-muted-foreground hover:text-soft"
                onClick={() => {
                  void browserCommand({ command: 'close', id: each.id })
                }}
                type="button"
              >
                <Close aria-hidden size={11} stroke={2} />
              </button>
            </div>
          ))}
          <IconButton
            icon={Plus}
            label={t.browser.newTab}
            onClick={() => {
              void browserCommand({ command: 'open' }).then(() => {
                address.current?.focus()
              })
            }}
            testId="browser-new"
          />
        </div>
      )}
      {invalid && (
        <p className="px-2 py-1 text-xs text-failed-text" role="alert">
          {t.browser.invalid}
        </p>
      )}
      {tab?.error && <p className="px-2 py-1 text-xs text-failed-text">{t.browser.failed(tab.error)}</p>}
      <div className="relative min-h-0 flex-1" data-testid="browser-box" ref={box}>
        {!tab && <p className="p-3 text-xs text-muted-foreground">{t.browser.empty}</p>}
      </div>
    </div>
  )
}
