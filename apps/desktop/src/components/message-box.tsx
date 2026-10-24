import { useStore } from '@nanostores/react'
import { type DragEvent, type KeyboardEvent, type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import type { AttachmentRow, SlashCommandRow } from '@/protocol/protocol.generated'
import { $pending, attach, detach, type Pending, putBack, takeReady } from '@/store/attachments'
import { offered, slashQuery } from '@/store/slash'

import { AttachmentChip } from './attachment-chip'
import { ArrowUp, Plus, Stop } from './ui/icons'
import { Tip } from './ui/tip'

/** "Auto" beside send: a conversation's auto mode, or an executor's own automatic mode. */
export interface AutoToggle {
  label: string
  on: boolean
  set: (on: boolean) => void
  /** What it does, on hover. */
  title: string
}

export interface MessageBoxProps {
  /** The box's name for screen readers, and what it says when empty. */
  label: string
  placeholder: string
  connected: boolean
  /** It takes nothing now (an executor busy and not taking corrections). */
  blocked?: boolean
  /** Send the words and the files that arrived; `false` when they did not reach the server, and
   * both come back to the box. */
  onSend: (text: string, attachments: AttachmentRow[]) => Promise<boolean>
  /** A message starting with `!`: a command for the terminal (step 16f). */
  onCommand?: (command: string) => void
  /** Whose chips the box holds; unset, the box takes no files. */
  owner?: string
  /** What sits below the box: the model, its effort, the terminal, what is particular to the screen. */
  toolbar: ReactNode
  /** A line above the box, such as a note on what the next prompt starts. */
  note?: ReactNode
  auto?: AutoToggle
  /** A turn runs: the button that stops it. */
  onStop?: () => void
  /** The commands `/` offers as the user types: an executor's as its harness describes them,
   * Atlas's own and its skills elsewhere. */
  commands?: SlashCommandRow[]
  /** A `/name` message the screen runs itself (Atlas's `/new`, `/stop`): `true` when it did, and
   * the message is not sent. */
  onSlash?: (name: string, rest: string) => boolean
  /** The `/` list opened: the screen may read its commands again (a skill written meanwhile). */
  onSlashOpen?: () => void
  /** Text to put back in the box (a stopped turn's unsent message), taken once. */
  restore?: () => string
  /** The textarea's test id, for the end-to-end tests. */
  testId?: string
}

/**
 * The one box of every conversation: Atlas's, a bot's and an executor's session (asked for by the
 * user on 16/10/2026). What it holds is the same everywhere: the chips of the files attached, "+" to
 * attach (where a clip was), the words, "Auto" (where a ⚡ was, asked for by the user on
 * 18/10/2026), send, which is stop while a turn runs and the box is empty, and the screen's
 * controls below it (asked for by the user on 22/10/2026, where they sat above). Each screen
 * gives only what is its own (`toolbar`, `auto`, `commands`), and whether it takes files.
 *
 * Files dropped on it, pasted into it or picked with "+" become chips above it (step 16g): each
 * starts sending at once, and the message goes with those that arrived.
 */
export function MessageBox(props: MessageBoxProps) {
  const {
    auto,
    blocked = false,
    commands = [],
    connected,
    label,
    note,
    onSlashOpen,
    onStop,
    owner,
    placeholder
  } = props
  const pending = useStore($pending)
  const [text, setText] = useState('')
  const [dragging, setDragging] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const chips = owner === undefined ? [] : (pending[owner] ?? [])
  const sending = chips.some(chip => chip.status === 'sending')
  const ready = chips.some(chip => chip.status === 'ready')
  // `/` and a name: the commands that match, in their groups; Escape hides them until the text
  // changes.
  const [hidden, setHidden] = useState<null | string>(null)
  const [active, setActive] = useState(0)
  const query = slashQuery(text)
  const groups = query === null || hidden === text ? [] : offered(commands, query)
  const rows = groups.flatMap(group => group.rows)
  const chosen = rows[Math.min(active, rows.length - 1)]
  const restore = props.restore
  // While a turn runs, the send button stops it, unless there are words or files to steer it with.
  const stopping = onStop !== undefined && !text.trim() && !ready

  const listing = query !== null

  useEffect(() => {
    if (listing) {
      onSlashOpen?.()
    }
  }, [listing, onSlashOpen])

  // The box grows with what is typed, up to its most, then scrolls (asked for by the user on
  // 22/10/2026, where a long prompt read through one line).
  useLayoutEffect(() => {
    const element = box.current

    if (element) {
      element.style.height = 'auto'
      element.style.height = `${String(element.scrollHeight + element.offsetHeight - element.clientHeight)}px`
    }
  }, [text])

  useEffect(() => {
    const back = restore?.()

    if (back) {
      setText(current => (current ? `${back}\n\n${current}` : back))
    }
  }, [restore])

  const submit = () => {
    const message = text.trim()

    if ((!message && !ready) || !connected || sending || blocked) {
      return
    }

    const slash = /^\/(\S+)\s*([\s\S]*)$/.exec(message)

    if (slash && props.onSlash?.(slash[1] ?? '', slash[2] ?? '') && !ready) {
      setText('')

      return
    }

    if (message.startsWith('!') && message.length > 1 && props.onCommand && !ready) {
      setText('')
      props.onCommand(message.slice(1))

      return
    }

    setText('')
    const attachments = owner === undefined ? [] : takeReady(owner)

    // A message that did not reach the server comes back to the box, its files too.
    void props.onSend(message, attachments).then(sent => {
      if (!sent) {
        setText(current => current || message)

        if (owner !== undefined) {
          putBack(owner, attachments)
        }
      }
    })
  }

  const takesFiles = owner !== undefined

  const take = (row: SlashCommandRow) => {
    setText(`/${row.name} `)
    setActive(0)
  }

  /** The list's keys while it shows: arrows move, Enter or Tab take the row, Escape hides it. */
  const onListKey = (event: KeyboardEvent): boolean => {
    if (!rows.length || event.nativeEvent.isComposing) {
      return false
    }

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const step = event.key === 'ArrowDown' ? 1 : rows.length - 1
      setActive(current => (Math.min(current, rows.length - 1) + step) % rows.length)
    } else if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
      if (!chosen) {
        return false
      }

      take(chosen)
    } else if (event.key === 'Escape') {
      setHidden(text)
    } else {
      return false
    }

    event.preventDefault()

    return true
  }

  return (
    <div
      className={`relative border-t border-neutral-200 p-4 dark:border-neutral-800 ${dragging ? 'bg-sky-50 dark:bg-sky-950/30' : ''}`}
      data-testid="composer"
      onDragLeave={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setDragging(false)
        }
      }}
      onDragOver={event => {
        if (takesFiles && event.dataTransfer.types.includes('Files')) {
          event.preventDefault()
          event.dataTransfer.dropEffect = 'copy'
          setDragging(true)
        }
      }}
      onDrop={(event: DragEvent) => {
        if (!takesFiles || !event.dataTransfer.types.includes('Files')) {
          return
        }

        event.preventDefault()
        setDragging(false)
        attach([...event.dataTransfer.files])
      }}
    >
      {dragging && (
        <div
          className="pointer-events-none absolute inset-2 z-10 grid place-items-center rounded-xl border-2 border-dashed border-sky-400 text-sm font-medium text-sky-700 dark:text-sky-300"
          data-testid="drop-zone"
        >
          {t.attachments.drop}
        </div>
      )}
      {note && <div className="mx-auto mb-2 max-w-3xl">{note}</div>}
      {groups.length > 0 && (
        <div
          aria-label={t.composer.slashCommands}
          className="mx-auto mb-2 max-h-72 max-w-3xl overflow-y-auto rounded-lg border border-neutral-200 p-1 text-sm dark:border-neutral-800"
          data-testid="slash-commands"
          role="listbox"
        >
          {groups.map(group => (
            <div data-group={group.group} key={group.group} role="group">
              <p className="px-2 pt-1 pb-0.5 text-xs font-semibold tracking-wide text-neutral-500 uppercase">
                {t.composer.slashGroups[group.group]}
              </p>
              {group.rows.map(row => (
                <button
                  aria-selected={row === chosen}
                  className={`flex w-full items-baseline gap-2 rounded px-2 py-1 text-left ${
                    row === chosen
                      ? 'bg-neutral-100 dark:bg-neutral-800'
                      : 'hover:bg-neutral-100 dark:hover:bg-neutral-800'
                  }`}
                  data-command={row.name}
                  data-testid="slash-command"
                  key={`${group.group}:${row.name}`}
                  onClick={() => {
                    take(row)
                  }}
                  onMouseEnter={() => {
                    setActive(rows.indexOf(row))
                  }}
                  role="option"
                  title={row.description || undefined}
                  type="button"
                >
                  <span className="shrink-0 font-mono font-medium">/{row.name}</span>
                  {row.hint && <span className="shrink-0 font-mono text-xs text-neutral-500">{row.hint}</span>}
                  {row.description && (
                    <span className="min-w-0 truncate text-xs text-neutral-500">{row.description}</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
      {chips.length > 0 && owner !== undefined && (
        <div
          className="mx-auto mb-2 flex max-h-36 max-w-3xl flex-wrap gap-2 overflow-y-auto pt-1.5 pr-1.5"
          data-testid="composer-attachments"
        >
          {chips.map(chip => (
            <AttachmentChip
              detail={detail(chip)}
              failed={chip.status === 'error'}
              key={chip.key}
              kind={chip.kind}
              name={chip.name}
              onRemove={() => {
                detach(owner, chip.key)
              }}
              progress={chip.status === 'sending' ? chip.progress : null}
              thumbnail={chip.thumbnail}
            />
          ))}
        </div>
      )}
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        {takesFiles && (
          <>
            <input
              className="hidden"
              data-testid="attachment-picker"
              multiple
              onChange={event => {
                attach([...(event.target.files ?? [])])
                event.target.value = ''
              }}
              ref={picker}
              type="file"
            />
            <Tip label={t.attachments.add}>
              <button
                aria-label={t.attachments.add}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-lg border border-neutral-300 text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                onClick={() => picker.current?.click()}
                type="button"
              >
                <Plus aria-hidden size={18} stroke={1.75} />
              </button>
            </Tip>
          </>
        )}
        <textarea
          aria-label={label}
          className="max-h-[40vh] min-h-11 flex-1 resize-none overflow-y-auto rounded-lg border border-neutral-300 bg-white px-3 py-2.5 dark:border-neutral-700 dark:bg-neutral-900"
          data-testid={props.testId}
          disabled={blocked}
          onChange={event => {
            setText(event.target.value)
            setActive(0)
          }}
          onKeyDown={event => {
            if (onListKey(event)) {
              return
            }

            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
          onPaste={event => {
            // A copied file or a screenshot attaches; text pastes as text.
            const files = [...event.clipboardData.files]

            if (takesFiles && files.length) {
              event.preventDefault()
              attach(files)
            }
          }}
          placeholder={placeholder}
          ref={box}
          rows={1}
          value={text}
        />
        {auto && (
          <Tip label={auto.title}>
            <button
              aria-label={auto.label}
              aria-pressed={auto.on}
              className={`h-11 shrink-0 rounded-lg border px-3 text-sm font-medium ${
                auto.on
                  ? 'border-amber-500 bg-amber-400 text-neutral-900'
                  : 'border-neutral-300 text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800'
              }`}
              onClick={() => {
                auto.set(!auto.on)
              }}
              type="button"
            >
              {t.composer.auto}
            </button>
          </Tip>
        )}
        {stopping ? (
          <Tip label={t.composer.stop}>
            <button
              aria-label={t.composer.stop}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
              data-testid="stop"
              onClick={onStop}
              type="button"
            >
              <Stop aria-hidden size={16} />
            </button>
          </Tip>
        ) : (
          <Tip label={sending ? t.attachments.waiting : t.composer.send}>
            <button
              aria-label={t.composer.send}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-neutral-900 text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900"
              disabled={!connected || sending || blocked || (!text.trim() && !ready)}
              onClick={submit}
              type="button"
            >
              <ArrowUp aria-hidden size={18} stroke={2} />
            </button>
          </Tip>
        )}
      </div>
      <div className="mx-auto mt-2 flex max-w-3xl flex-wrap items-center gap-2 text-sm" data-testid="composer-toolbar">
        {props.toolbar}
      </div>
    </div>
  )
}

function detail(chip: Pending): string {
  switch (chip.status) {
    case 'error':
      return chip.error
    case 'sending':
      return t.attachments.uploading(Math.round(chip.progress * 100))
    case 'ready':
      return t.attachments.size(chip.size)
  }
}
