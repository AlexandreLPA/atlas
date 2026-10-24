import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { t } from '@/i18n'
import type { Soul } from '@/protocol/protocol.generated'
import { $home, readSoul, writeSoul } from '@/store/app'

/**
 * The main agent's soul (docs/ARCHITECTURE.md, The soul): a `SOUL.md` editor, its text, the
 * unsaved mark and Save,
 * with Atlas's default shown while the soul is blank and the note that a change applies from the
 * next conversation. The soul is read again whenever the screen opens or the home changes, so an
 * edit the agent made is what the user sees. A part of the settings screen's Personalization since
 * step 15b.
 */
export function SoulSection() {
  const home = useStore($home)
  const [soul, setSoul] = useState<null | Soul>(null)
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    let live = true
    setSoul(null)
    setError(null)
    setSaved(false)
    readSoul().then(
      read => {
        if (live) {
          setSoul(read)
          setText(read.text)
        }
      },
      (failure: unknown) => {
        if (live) {
          setError(failure instanceof Error ? failure.message : String(failure))
        }
      }
    )

    return () => {
      live = false
    }
  }, [home])

  const dirty = soul !== null && text !== soul.text

  const save = async () => {
    setSaving(true)
    setError(null)

    try {
      const written = await writeSoul(text)
      setSoul(written)
      setText(written.text)
      setSaved(true)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="space-y-3" data-testid="soul-view">
      <div className="space-y-4">
        <h3 className="text-sm font-semibold">{t.soul.title}</h3>
        <p className="text-sm text-neutral-500">{soul && !soul.exists ? t.soul.missing : t.soul.intro}</p>
        {error && (
          <p
            className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
            role="alert"
          >
            {error}
          </p>
        )}
        {soul === null ? (
          !error && <p className="text-sm text-neutral-500">{t.soul.loading}</p>
        ) : (
          <>
            <div className="flex items-baseline justify-between gap-2 text-xs text-neutral-500">
              <code className="truncate" data-testid="soul-path" title={soul.path}>
                {soul.path}
              </code>
              {dirty && <span data-testid="soul-unsaved">{t.soul.unsaved}</span>}
            </div>
            <textarea
              aria-label="SOUL.md"
              className="min-h-56 w-full rounded-md border border-neutral-300 bg-white p-3 font-mono text-sm dark:border-neutral-700 dark:bg-neutral-900"
              data-testid="soul-text"
              onChange={event => {
                setText(event.target.value)
                setSaved(false)
              }}
              placeholder={t.soul.placeholder}
              spellCheck={false}
              value={text}
            />
            {text.trim() === '' && (
              <div className="space-y-1 text-sm text-neutral-500" data-testid="soul-default">
                <p>{t.soul.blank}</p>
                <p className="rounded-md bg-neutral-100 p-3 whitespace-pre-wrap dark:bg-neutral-900">{soul.default}</p>
              </div>
            )}
            <div className="flex items-center justify-end gap-3">
              {saved && !dirty && (
                <span className="text-sm text-neutral-500" data-testid="soul-saved" role="status">
                  {t.soul.saved}
                </span>
              )}
              <button
                className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900"
                data-testid="soul-save"
                disabled={!dirty || saving}
                onClick={() => void save()}
                type="button"
              >
                {saving ? t.soul.saving : t.soul.save}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
