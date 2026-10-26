import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { KeyProvider } from '@/protocol/protocol.generated'
import { $keyNotice, $keyProviders, chooseKeyLater, connectProvider, dismissKeyNotice } from '@/store/app'

import { ExecutorOffers } from './executor-offers'

/**
 * The key screen (docs/ARCHITECTURE.md, The installer): what the app opens on when no provider has a
 * key in the vault, so a first run needs no terminal: pick a provider, follow its "get a key" link,
 * paste the key.
 *
 * The field takes any text that is not blank, so an unusual key never blocks
 * anyone; the server checks it where it costs nothing and keeps out only a key the provider refuses.
 * The key is sent once and never comes back. "Choose later" gives way to a band, so a user who
 * skipped is never left with no way forward.
 */
export function KeyScreen() {
  const providers = useStore($keyProviders)
  const notice = useStore($keyNotice)

  return (
    <div className="flex flex-1 items-start justify-center overflow-y-auto p-6" data-testid="key-screen">
      <div className="mt-[8vh] w-full max-w-lg space-y-5">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold">{t.keys.title}</h1>
          <p className="text-sm text-muted-foreground">{t.keys.intro}</p>
        </div>
        {notice ? (
          <div className="space-y-3 rounded-lg border border-waiting/40 bg-waiting/10 p-4 text-sm">
            <p className="font-medium">{t.keys.saved}</p>
            <p data-testid="key-notice">{notice.detail}</p>
            <button className={PRIMARY} data-testid="key-continue" onClick={dismissKeyNotice} type="button">
              {t.keys.continue}
            </button>
          </div>
        ) : (
          providers && <Providers providers={providers} />
        )}
        {!notice && <ExecutorOffers />}
        {!notice && (
          <button
            className="text-sm text-muted-foreground hover:text-foreground"
            data-testid="key-later"
            onClick={() => {
              chooseKeyLater(true)
            }}
            type="button"
          >
            {t.keys.later}
          </button>
        )}
      </div>
    </div>
  )
}

/** The provider the screen calls a good first choice: one key, hundreds of models. */
const FIRST_CHOICE = 'openrouter'

/** The providers, each with its key field: the key screen's, and the settings screen's Model. The
 * one called a good first choice comes first, open (the UI review, 26/10/2026: it said so second,
 * under another one open). */
export function Providers({ providers }: { providers: KeyProvider[] }) {
  const listed = [...providers].sort((a, b) => Number(b.id === FIRST_CHOICE) - Number(a.id === FIRST_CHOICE))
  const [picked, setPicked] = useState(listed[0]?.id ?? '')

  return (
    <div className="space-y-2" role="radiogroup">
      {listed.map(provider => (
        <ProviderRow
          key={provider.id}
          onPick={() => {
            setPicked(provider.id)
          }}
          open={provider.id === picked}
          provider={provider}
        />
      ))}
    </div>
  )
}

type RowProps = { onPick: () => void; open: boolean; provider: KeyProvider }

function ProviderRow({ onPick, open, provider }: RowProps) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const description = t.keys.describe[provider.id]

  const connect = () => {
    setBusy(true)
    setError('')
    connectProvider(provider.id, key)
      .then(found => {
        if (found.stored) {
          setKey('')
        } else {
          setError(found.detail)
        }
      })
      .catch((failure: unknown) => {
        setError(t.keys.failed(failure instanceof Error ? failure.message : String(failure)))
      })
      .finally(() => {
        setBusy(false)
      })
  }

  return (
    <div
      className={`rounded-lg border p-3 ${open ? 'border-border-strong' : 'border-border'}`}
      data-testid={`key-provider-${provider.id}`}
    >
      <button
        aria-checked={open}
        className="flex w-full items-baseline gap-2 text-left"
        onClick={onPick}
        role="radio"
        type="button"
      >
        <span className="font-medium">{provider.name}</span>
        {provider.stored && <span className="text-xs text-muted-foreground">{t.keys.stored}</span>}
        {description && <span className="truncate text-sm text-muted-foreground">{description}</span>}
      </button>
      {open && (
        <form
          className="mt-3 space-y-2"
          onSubmit={event => {
            event.preventDefault()
            connect()
          }}
        >
          <div className="flex gap-2">
            <input
              aria-label={t.keys.placeholder}
              autoComplete="off"
              autoFocus
              className="w-full rounded-md border border-border-strong bg-background px-2 py-1.5 font-mono text-sm"
              data-testid="key-input"
              disabled={busy}
              onChange={event => {
                setKey(event.target.value)
              }}
              placeholder={t.keys.placeholder}
              spellCheck={false}
              type="password"
              value={key}
            />
            <button className={PRIMARY} data-testid="key-connect" disabled={busy || !key.trim()} type="submit">
              {busy ? t.keys.checking : t.keys.connect}
            </button>
          </div>
          {provider.key_page && (
            // The main process sends a link the page opens to the system browser.
            <a className="link text-sm" href={provider.key_page} rel="noreferrer" target="_blank">
              {t.keys.getKey}
            </a>
          )}
          {error && (
            <p className="text-sm text-failed-text" data-testid="key-error" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  )
}

/** Where the key screen goes after "choose later": a band that leads back to it. */
export function KeyBand() {
  return (
    <div
      className="flex items-center gap-3 border-b border-border bg-subtle px-4 py-2 text-sm text-foreground"
      data-testid="key-band"
      role="status"
    >
      <span>{t.keys.band}</span>
      <button
        className="font-medium underline"
        data-testid="key-band-connect"
        onClick={() => {
          chooseKeyLater(false)
        }}
        type="button"
      >
        {t.keys.bandAction}
      </button>
    </div>
  )
}

const PRIMARY =
  'shrink-0 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40'
