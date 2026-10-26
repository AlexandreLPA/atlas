import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import type { CommandRow, SubagentRow } from '@/protocol/protocol.generated'
import {
  $commands,
  $conversations,
  $selected,
  $subagents,
  commandOutput,
  steerSubagent,
  stopCommand,
  stopSubagent
} from '@/store/app'
import { commandName } from '@/store/commands'
import { elapsed } from '@/store/subagents'

import { PILL } from './ui/pill'

/** How often an open background task reads its output again while it runs. */
const OUTPUT_EVERY_MS = 1500

/** The conversation's running subagents; an outside executor shows apart, on the workbench. */
function useRunning(): SubagentRow[] {
  const selected = useStore($selected)
  const all = useStore($subagents)

  return ((selected ? all[selected] : undefined) ?? []).filter(row => !row.executor)
}

/** The conversation's background commands: the running ones, and the last that ended. */
function useCommands(): CommandRow[] {
  const selected = useStore($selected)
  const all = useStore($commands)

  return (selected ? all[selected] : undefined) ?? []
}

/**
 * The conversation's running subagents and background commands behind a pill in the box, only while
 * some run: an agents pill and an agent map (decided with the user on 12/10/2026; the
 * background tasks on 14/10/2026). The pill tells how many; a click opens the map, the conversation
 * on the left and each subagent on the right with its goal, time and tools, and the background tasks
 * below. A click on a subagent shows the tool it runs or ran last, with Steer (a correction that
 * enters after its current tools) and Stop (what it had still comes back); a click on a task shows
 * its command, the end of its output, and Stop. The map closes by itself when nothing is left on it.
 */
export function AgentsButton() {
  const rows = useRunning()
  const commands = useCommands()
  const running = commands.filter(row => row.status === 'running').length
  const [open, setOpen] = useState(false)

  if (!open && rows.length === 0 && running === 0) {
    return null
  }

  return (
    <>
      {(rows.length > 0 || running > 0) && (
        <button
          aria-haspopup="dialog"
          className={PILL}
          data-testid="agents-button"
          onClick={() => {
            setOpen(true)
          }}
          type="button"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-working motion-safe:animate-pulse" />
          {t.subagents.button(rows.length, running)}
        </button>
      )}
      {open && (
        <AgentMap
          commands={commands}
          onClose={() => {
            setOpen(false)
          }}
          rows={rows}
        />
      )}
    </>
  )
}

function AgentMap({ commands, onClose, rows }: { commands: CommandRow[]; onClose: () => void; rows: SubagentRow[] }) {
  const selected = useStore($selected)
  const conversations = useStore($conversations)
  const title = conversations.find(row => row.id === selected)?.title || t.conversations.untitled
  const [now, setNow] = useState(() => Date.now())
  const [focused, setFocused] = useState<null | string>(null)
  // The tasks that ran while the map was open: one that ends stays, to show how it ended.
  const [seen, setSeen] = useState<ReadonlySet<string>>(
    () => new Set(commands.filter(row => row.status === 'running').map(row => row.command_id))
  )
  const tasks = commands.filter(row => seen.has(row.command_id))

  useEffect(() => {
    const started = commands.filter(row => row.status === 'running' && !seen.has(row.command_id))

    if (started.length > 0) {
      setSeen(new Set([...seen, ...started.map(row => row.command_id)]))
    }
  }, [commands, seen])

  useEffect(() => {
    if (rows.length === 0 && tasks.length === 0) {
      onClose()
    }
  }, [onClose, rows.length, tasks.length])

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now())
    }, 1000)
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', escape)

    return () => {
      window.clearInterval(timer)
      window.removeEventListener('keydown', escape)
    }
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-6"
      onClick={event => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
    >
      <section
        aria-label={t.subagents.map}
        aria-modal="true"
        className="flex max-h-full w-full max-w-3xl flex-col rounded-xl border border-border bg-raised text-sm shadow-xl"
        data-testid="agent-map"
        role="dialog"
      >
        <header className="flex items-start justify-between px-5 pt-4 pb-3">
          <div>
            <h2 className="text-base font-semibold">{t.subagents.map}</h2>
            {rows.length > 0 && <p className="text-xs text-muted-foreground">{t.subagents.hint(rows.length)}</p>}
          </div>
          <button
            aria-label={t.subagents.close}
            className="rounded px-2 py-0.5 text-lg leading-none text-muted-foreground hover:bg-muted"
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </header>
        <div className="min-h-0 overflow-y-auto px-5 pb-5">
          {rows.length > 0 && (
            <div className="flex items-center gap-0">
              <div className="w-56 shrink-0 rounded-lg border border-border-strong px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 shrink-0 rounded-full bg-muted-foreground" />
                  <span className="truncate font-medium" title={title}>
                    {title}
                  </span>
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">{t.subagents.parent}</p>
              </div>
              <span className="h-px w-5 shrink-0 bg-border-strong" />
              <ul className="min-w-0 flex-1 space-y-2 border-l border-border-strong py-1">
                {rows.map(row => (
                  <AgentNode
                    focused={focused === row.subagent_id}
                    key={row.subagent_id}
                    now={now}
                    onFocus={() => {
                      setFocused(focused === row.subagent_id ? null : row.subagent_id)
                    }}
                    row={row}
                  />
                ))}
              </ul>
            </div>
          )}
          {tasks.length > 0 && (
            <section className={rows.length > 0 ? 'mt-5' : ''}>
              <p className="mb-2 text-xs text-muted-foreground">{t.subagents.tasks(tasks.length)}</p>
              <ul className="space-y-2">
                {tasks.map(row => (
                  <TaskNode
                    focused={focused === `task:${row.command_id}`}
                    key={row.command_id}
                    now={now}
                    onFocus={() => {
                      const key = `task:${row.command_id}`
                      setFocused(focused === key ? null : key)
                    }}
                    row={row}
                  />
                ))}
              </ul>
            </section>
          )}
        </div>
      </section>
    </div>
  )
}

function AgentNode({
  focused,
  now,
  onFocus,
  row
}: {
  focused: boolean
  now: number
  onFocus: () => void
  row: SubagentRow
}) {
  const [steering, setSteering] = useState(false)
  const [text, setText] = useState('')
  const [refused, setRefused] = useState(false)

  const steer = () => {
    const message = text.trim()

    if (!message) {
      return
    }

    void steerSubagent(row.subagent_id, message)
      .then(accepted => {
        setRefused(!accepted)

        if (accepted) {
          setText('')
          setSteering(false)
        }
      })
      .catch(() => {
        setRefused(true)
      })
  }

  return (
    <li className="flex items-start" data-testid="subagent-row">
      <span className="mt-5 h-px w-4 shrink-0 bg-border-strong" />
      <div className="min-w-0 flex-1 rounded-lg border border-border-strong">
        <button
          aria-expanded={focused}
          className="w-full px-3 py-2 text-left hover:bg-hover"
          onClick={onFocus}
          type="button"
        >
          <span className="flex items-center gap-2">
            <span className="h-2 w-2 shrink-0 rounded-full bg-working motion-safe:animate-pulse" />
            <span className="min-w-0 flex-1 truncate" title={row.goal}>
              {row.goal}
            </span>
          </span>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            {elapsed(row.started_at, now)} · {t.subagents.tools(row.tools)}
          </span>
        </button>
        {focused && (
          <div className="border-t border-border px-3 py-2">
            <p className="text-xs whitespace-pre-wrap text-soft">{row.goal}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {row.model} · {row.last_tool ?? t.subagents.noTool}
            </p>
            <div className="mt-2 flex gap-2">
              <button
                className="rounded border border-border-strong px-2 py-0.5 text-xs"
                onClick={() => {
                  setSteering(!steering)
                }}
                type="button"
              >
                {t.subagents.steer}
              </button>
              <button
                className="rounded border border-border-strong px-2 py-0.5 text-xs"
                onClick={() => void stopSubagent(row.subagent_id)}
                type="button"
              >
                {t.subagents.stop}
              </button>
            </div>
            {steering && (
              <div className="mt-2 flex gap-2">
                <input
                  aria-label={t.subagents.steer}
                  autoFocus
                  className="flex-1 rounded border border-border-strong bg-raised px-2 py-1"
                  onChange={event => {
                    setText(event.target.value)
                  }}
                  onKeyDown={event => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      steer()
                    }
                  }}
                  placeholder={t.subagents.steerPlaceholder}
                  value={text}
                />
                <button
                  className="rounded bg-primary px-3 py-1 text-xs font-medium text-primary-foreground disabled:opacity-40"
                  disabled={!text.trim()}
                  onClick={steer}
                  type="button"
                >
                  {t.subagents.send}
                </button>
              </div>
            )}
            {refused && <p className="mt-1 text-xs text-waiting-text">{t.subagents.refused}</p>}
          </div>
        )}
      </div>
    </li>
  )
}

/**
 * A background task on the map: its name, id and time; open, its command, the end of its output
 * (read again while it runs) and Stop, which hands the output back.
 */
function TaskNode({
  focused,
  now,
  onFocus,
  row
}: {
  focused: boolean
  now: number
  onFocus: () => void
  row: CommandRow
}) {
  const [output, setOutput] = useState('')
  const [gone, setGone] = useState(false)
  const shown = useRef<HTMLPreElement>(null)
  const running = row.status === 'running'
  const until = row.ended_at ? Date.parse(row.ended_at) : now
  const status = t.subagents.taskStatus(row.status, row.exit_code)
  const dot = running ? 'bg-working motion-safe:animate-pulse' : row.status === 'failed' ? 'bg-failed' : 'bg-faint'

  useEffect(() => {
    if (!focused) {
      return
    }

    let alive = true
    const read = () => {
      void commandOutput(row.command_id)
        .then(found => {
          if (alive && found.found) {
            setOutput(found.text)
          }
        })
        .catch(() => undefined)
    }
    read()
    // Once ended, it is read once more, for how it ended.
    const timer = running ? window.setInterval(read, OUTPUT_EVERY_MS) : undefined

    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [focused, running, row.command_id])

  useEffect(() => {
    const pre = shown.current

    if (pre) {
      pre.scrollTop = pre.scrollHeight
    }
  }, [output])

  const stop = () => {
    void stopCommand(row.command_id)
      .then(result => {
        setGone(!result.stopped)

        if (result.stopped) {
          setOutput(result.text)
        }
      })
      .catch(() => {
        setGone(true)
      })
  }

  return (
    <li className="rounded-lg border border-border-strong" data-testid="task-row">
      <button
        aria-expanded={focused}
        className="w-full px-3 py-2 text-left hover:bg-hover"
        onClick={onFocus}
        type="button"
      >
        <span className="flex items-center gap-2">
          <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
          <span className="min-w-0 flex-1 truncate" title={row.command}>
            {commandName(row)}
          </span>
        </span>
        <span className="mt-0.5 block text-xs text-muted-foreground">
          {row.command_id} · {running ? '' : `${status} · `}
          {elapsed(row.started_at, until)}
        </span>
      </button>
      {focused && (
        <div className="border-t border-border px-3 py-2">
          <p className="text-xs text-muted-foreground">
            {status} · {elapsed(row.started_at, until)}
          </p>
          <pre className="mt-1 font-mono text-xs break-all whitespace-pre-wrap text-soft">{row.command}</pre>
          <p className="mt-2 text-xs font-medium">{t.subagents.output}</p>
          <pre
            className="mt-1 max-h-64 overflow-auto rounded bg-subtle p-2 font-mono text-xs break-all whitespace-pre-wrap"
            data-testid="task-output"
            ref={shown}
          >
            {output || t.subagents.noOutput}
          </pre>
          {running && (
            <button
              className="mt-2 rounded border border-border-strong px-2 py-0.5 text-xs"
              onClick={stop}
              type="button"
            >
              {t.subagents.stop}
            </button>
          )}
          {gone && <p className="mt-1 text-xs text-waiting-text">{t.subagents.taskGone}</p>}
        </div>
      )}
    </li>
  )
}
