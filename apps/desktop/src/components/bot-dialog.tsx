import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useState } from 'react'

import { t } from '@/i18n'
import type { BotDefinition, BotOptions } from '@/protocol/protocol.generated'
import { $botDialog, $modelOptions, type BotDialog, botOptions, closeBotDialog, deleteBot, saveBot } from '@/store/app'

import { BotFace } from './bot-face'

/**
 * The dialog a bot is made and changed in, kept short (decided on
 * 13/10/2026). The quick path is three fields under the face the name draws: name, title and what
 * the bot helps with; "Advanced" opens the rest: its model, its mission in the user's own words
 * (left blank, one is composed from the title and the description), and
 * the tools, MCP servers and library skills it may use. A bot's name is its identity and cannot
 * change; deleting asks twice.
 */
export function BotDialogHost() {
  const dialog = useStore($botDialog)

  // A key per dialog, so a new one never starts with the last one's fields.
  return dialog ? <Dialog dialog={dialog} key={`${dialog.mode}:${dialog.definition?.name ?? ''}`} /> : null
}

/** Turn what was typed into a name a bot may have: lowercase letters, digits, - and _. */
export function slug(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^[-_]+/, '')
    .replace(/-+$/, '')
    .slice(0, 64)
}

function Dialog({ dialog }: { dialog: BotDialog }) {
  const creating = dialog.mode === 'create'
  const start = dialog.definition
  const [name, setName] = useState(start?.name ?? '')
  const [title, setTitle] = useState(start?.title ?? '')
  const [description, setDescription] = useState(start?.description ?? '')
  const [mission, setMission] = useState(start?.mission ?? '')
  const [model, setModel] = useState(start?.model ?? '')
  const [tools, setTools] = useState<null | string[]>(start?.tools ?? null)
  const [servers, setServers] = useState<null | string[]>(start?.mcp_servers ?? null)
  const [skills, setSkills] = useState<null | string[]>(start?.skills ?? null)
  const [advanced, setAdvanced] = useState(false)
  const [options, setOptions] = useState<BotOptions | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const models = useStore($modelOptions)
  const handle = creating ? slug(name) : name

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeBotDialog()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const openAdvanced = () => {
    setAdvanced(!advanced)

    if (!options) {
      void botOptions()
        .then(setOptions)
        .catch(() => undefined)
    }
  }

  const run = (action: () => Promise<void>) => {
    setBusy(true)
    setError('')
    action()
      .catch((failure: unknown) => {
        setError(failure instanceof Error ? failure.message : String(failure))
      })
      .finally(() => {
        setBusy(false)
      })
  }

  const save = () => {
    const definition: BotDefinition = {
      description: description.trim(),
      mcp_servers: servers,
      mission: mission.trim(),
      model: model || null,
      name: handle,
      skills,
      title: title.trim(),
      tools
    }
    run(() => saveBot(definition, creating))
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4"
      data-testid="bot-dialog"
      onMouseDown={event => {
        if (event.target === event.currentTarget) {
          closeBotDialog()
        }
      }}
      role="presentation"
    >
      <form
        aria-label={creating ? t.bots.newTitle : t.bots.editTitle}
        className={`flex max-h-full w-full flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-raised p-5 shadow-xl ${
          advanced ? 'max-w-2xl' : 'max-w-md'
        }`}
        onSubmit={event => {
          event.preventDefault()
          save()
        }}
        role="dialog"
      >
        <div>
          <h2 className="text-base font-semibold">{creating ? t.bots.newTitle : t.bots.editTitle}</h2>
          <p className="text-sm text-muted-foreground">{t.bots.intro}</p>
        </div>
        <div className="flex justify-center py-1">
          <BotFace name={handle || 'bot'} size={56} title={title || name} />
        </div>
        <Field hint={creating && handle && handle !== name ? `@${handle}` : undefined} label={t.bots.name}>
          <input
            autoFocus={creating}
            className={INPUT}
            data-testid="bot-name"
            disabled={!creating}
            onChange={event => {
              setName(event.target.value)
            }}
            placeholder="inbox-triage"
            value={name}
          />
        </Field>
        <Field label={t.bots.title}>
          <input
            className={INPUT}
            data-testid="bot-title"
            maxLength={60}
            onChange={event => {
              setTitle(event.target.value)
            }}
            placeholder="Inbox Triage"
            value={title}
          />
        </Field>
        <Field hint={`${description.length}/160`} label={t.bots.description}>
          <textarea
            className={`${INPUT} min-h-16`}
            data-testid="bot-description"
            maxLength={160}
            onChange={event => {
              setDescription(event.target.value)
            }}
            placeholder={t.bots.descriptionPlaceholder}
            value={description}
          />
        </Field>
        <button
          aria-expanded={advanced}
          className="self-start text-sm text-muted-foreground hover:text-foreground"
          data-testid="bot-advanced"
          onClick={openAdvanced}
          type="button"
        >
          {advanced ? '▾' : '▸'} {t.bots.advanced}
        </button>
        {advanced && (
          <div className="space-y-3 rounded-md border border-border p-3">
            <Field label={t.bots.model}>
              <select
                className={INPUT}
                data-testid="bot-model"
                onChange={event => {
                  setModel(event.target.value)
                }}
                value={model}
              >
                <option value="">{t.bots.modelOfHome}</option>
                {model && !known(models?.providers, model) && <option value={model}>{model}</option>}
                {models?.providers.map(provider => (
                  <optgroup key={provider.id} label={provider.name}>
                    {provider.models.map(option => (
                      <option key={option.id} value={`${provider.id}/${option.id}`}>
                        {option.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </Field>
            <Field hint={t.bots.missionHint} label={t.bots.mission}>
              <textarea
                className={`${INPUT} min-h-32 font-mono text-xs`}
                data-testid="bot-mission"
                onChange={event => {
                  setMission(event.target.value)
                }}
                value={mission}
              />
            </Field>
            <Allowed all={options?.tools} label={t.bots.tools} onChange={setTools} picked={tools} testId="bot-tools" />
            <Allowed
              all={options?.mcp_servers}
              label={t.bots.mcp}
              onChange={setServers}
              picked={servers}
              testId="bot-mcp"
            />
            <Allowed
              all={options?.skills}
              label={t.bots.skills}
              onChange={setSkills}
              picked={skills}
              testId="bot-skills"
            />
          </div>
        )}
        {error && (
          <p className="text-sm text-failed-text" data-testid="bot-error" role="alert">
            {error}
          </p>
        )}
        <div className="flex items-center justify-between gap-2 pt-1">
          <div>
            {!creating &&
              (deleting ? (
                <button
                  className={`${BUTTON} text-failed-text`}
                  data-testid="bot-delete-confirm"
                  disabled={busy}
                  onClick={() => {
                    run(() => deleteBot(name))
                  }}
                  type="button"
                >
                  {t.bots.confirmDelete}
                </button>
              ) : (
                <button
                  className={BUTTON}
                  data-testid="bot-delete"
                  onClick={() => {
                    setDeleting(true)
                  }}
                  type="button"
                >
                  {t.bots.delete}
                </button>
              ))}
          </div>
          <div className="flex gap-2">
            <button className={BUTTON} onClick={closeBotDialog} type="button">
              {t.bots.cancel}
            </button>
            <button
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
              data-testid="bot-save"
              disabled={busy || !handle}
              type="submit"
            >
              {creating ? t.bots.create : t.bots.save}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}

function known(providers: undefined | { id: string; models: { id: string }[] }[], model: string): boolean {
  return (providers ?? []).some(provider => provider.models.some(option => `${provider.id}/${option.id}` === model))
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

type AllowedProps = {
  /** Everything the home has of this kind; `undefined` while it is read. */
  all: string[] | undefined
  label: string
  onChange: (picked: null | string[]) => void
  /** What the bot may use; `null`, everything the home has. */
  picked: null | string[]
  testId: string
}

/**
 * What a bot may use of one kind: everything the home has, or only what is ticked. A name the
 * definition lists that the home no longer has stays listed, so saving never drops it unseen.
 */
function Allowed({ all, label, onChange, picked, testId }: AllowedProps) {
  const names = [...new Set([...(all ?? []), ...(picked ?? [])])].sort()

  return (
    <fieldset className="space-y-1 text-sm" data-testid={testId}>
      <legend className="font-medium">{label}</legend>
      <label className="flex items-center gap-2">
        <input
          checked={picked === null}
          onChange={event => {
            onChange(event.target.checked ? null : names)
          }}
          type="checkbox"
        />
        {t.bots.everything}
      </label>
      {picked !== null && (
        <div className="grid max-h-40 grid-cols-2 gap-x-3 gap-y-1 overflow-y-auto pl-5 sm:grid-cols-3">
          {names.length === 0 && <span className="text-muted-foreground">{t.bots.nothingToPick}</span>}
          {names.map(name => (
            <label className="flex items-center gap-2 truncate" key={name} title={name}>
              <input
                checked={picked.includes(name)}
                onChange={event => {
                  onChange(event.target.checked ? [...picked, name] : picked.filter(kept => kept !== name))
                }}
                type="checkbox"
              />
              <span className="truncate font-mono text-xs">{name}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  )
}

const INPUT = 'w-full rounded-md border border-border-strong bg-background px-2 py-1.5 text-sm disabled:opacity-60'

const BUTTON = 'rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-40'
