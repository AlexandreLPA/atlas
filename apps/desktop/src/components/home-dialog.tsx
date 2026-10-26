import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useState } from 'react'

import { locale, t } from '@/i18n'
import { $homeDialog, $homes, closeHomeDialog, createHome, type HomeDialog, importHome } from '@/store/app'

import { slug } from './bot-dialog'

/**
 * The homes' dialog (step 10c, docs/ARCHITECTURE.md, Homes in the app): a new home (a name, and
 * "Copy from", which starts on the default home);
 * or a backup the server checked, saying where it came from and asking the new home's name, which
 * starts on one no home has. An import named after its archive would fail on a name that
 * exists; the default home's backup is the common one, and `default` always exists.
 */
export function HomeDialogHost() {
  const dialog = useStore($homeDialog)

  // A key per dialog, so a new one never starts with the last one's fields.
  return dialog ? <Dialog dialog={dialog} key={dialog.mode === 'import' ? dialog.path : 'create'} /> : null
}

function Dialog({ dialog }: { dialog: HomeDialog }) {
  const homes = useStore($homes)
  const importing = dialog.mode === 'import'
  const [name, setName] = useState(importing ? dialog.info.suggested_name : '')
  const [cloneFrom, setCloneFrom] = useState('default')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const handle = slug(name)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) {
        closeHomeDialog()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [busy])

  const submit = () => {
    setBusy(true)
    setError('')
    const done = importing ? importHome(dialog.path, handle) : createHome(handle, cloneFrom || null)
    done
      .catch((failure: unknown) => {
        setError(failure instanceof Error ? failure.message : String(failure))
      })
      .finally(() => {
        setBusy(false)
      })
  }

  const title = importing ? t.homes.importTitle : t.homes.newTitle

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4"
      data-testid="home-dialog"
      onMouseDown={event => {
        if (event.target === event.currentTarget && !busy) {
          closeHomeDialog()
        }
      }}
      role="presentation"
    >
      <form
        aria-label={title}
        className="flex max-h-full w-full max-w-md flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-raised p-5 shadow-xl"
        onSubmit={event => {
          event.preventDefault()
          submit()
        }}
        role="dialog"
      >
        <div>
          <h2 className="text-base font-semibold">{title}</h2>
          <p className="text-sm text-muted-foreground" data-testid={importing ? 'home-import-from' : undefined}>
            {importing
              ? t.homes.importFrom(
                  dialog.info.home,
                  new Date(dialog.info.created_at).toLocaleString(locale),
                  dialog.info.atlas,
                  dialog.info.conversations
                )
              : t.homes.intro}
          </p>
        </div>
        <Field
          hint={handle && handle !== name ? handle : t.homes.nameHint}
          label={importing ? t.homes.newName : t.homes.name}
        >
          <input
            autoFocus
            className={INPUT}
            data-testid="home-name"
            onChange={event => {
              setName(event.target.value)
            }}
            placeholder="work"
            value={name}
          />
        </Field>
        {importing ? (
          <p className="text-sm text-soft">{t.homes.importHint}</p>
        ) : (
          <Field label={t.homes.clone}>
            <select
              className={INPUT}
              data-testid="home-clone"
              onChange={event => {
                setCloneFrom(event.target.value)
              }}
              value={cloneFrom}
            >
              {homes.map(row => (
                <option key={row.name} value={row.name}>
                  {row.name}
                </option>
              ))}
              <option value="">{t.homes.cloneNone}</option>
            </select>
            <span className="block text-xs text-muted-foreground">
              {cloneFrom ? t.homes.cloneHint : t.homes.emptyHint}
            </span>
          </Field>
        )}
        {error && (
          <p className="text-sm break-words text-failed-text" data-testid="home-error" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button className={BUTTON} disabled={busy} onClick={closeHomeDialog} type="button">
            {t.homes.cancel}
          </button>
          <button
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            data-testid="home-submit"
            disabled={busy || !handle}
            type="submit"
          >
            {busy
              ? importing
                ? t.homes.importing
                : t.homes.creating
              : importing
                ? t.homes.importAction
                : t.homes.create}
          </button>
        </div>
      </form>
    </div>
  )
}

function Field({ children, hint, label }: { children: ReactNode; hint?: string; label: string }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="flex items-baseline justify-between gap-2">
        <span className="font-medium">{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </span>
      {children}
    </label>
  )
}

const INPUT = 'w-full rounded-md border border-border-strong bg-background px-2 py-1.5 text-sm disabled:opacity-60'

const BUTTON = 'rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-40'
