import { useStore } from '@nanostores/react'
import { type ReactNode, useState } from 'react'

import { t } from '@/i18n'
import { $selected, openDelegated } from '@/store/app'
import type { Item, ToolStatus } from '@/store/thread'
import type { Waiting } from '@/store/waiting'

import { elapsed, useNow } from './use-now'

type Tool = Extract<Item, { kind: 'tool' }>

/** What a call's row shows: how it stands, or that a card holds it (`waiting call`). */
export type Shown = 'waiting' | ToolStatus

/** Past it the output is cut on screen, and Copy all gives it whole. */
export const MAX_SHOWN_CHARS = 20_000

/**
 * A tool call on the trail. Closed, one line: its title, or its name and a summary of its
 * arguments, and how it stands (a running timer, what it waits for, its time, or why it did not
 * complete; success carries no mark). Open, a box with what went in and what came out (IN and
 * OUT). `waiting` is what a card holding the call waits for: then it has not run, or waits on the
 * user to go on, and never reads as running.
 */
export function ToolRow({ tool, waiting }: { tool: Tool; waiting?: Waiting }) {
  const shown: Shown = waiting && tool.status === 'running' ? 'waiting' : tool.status
  const now = useNow(shown === 'running' && tool.startedAt !== null)
  const title = toolTitle(tool.name, tool.args, shown)
  const input = inputFields(tool.args)

  return (
    <details className="group text-sm" data-status={shown} data-testid="tool-row">
      <summary className="flex min-w-0 cursor-pointer list-none items-center gap-2 select-none [&::-webkit-details-marker]:hidden">
        {title ? (
          <span className="min-w-0 truncate">
            <span className="font-medium">{title.action}</span>{' '}
            <span className="font-mono text-soft">{title.target}</span>
          </span>
        ) : (
          <>
            <span className="flex-none font-mono font-semibold">{tool.name}</span>
            <span className="min-w-0 truncate font-mono text-muted-foreground">{summarize(tool.args)}</span>
          </>
        )}
        <span className={`ml-auto flex-none text-xs ${STATE_COLOR[shown]}`} data-testid="tool-state">
          {stateText(tool, shown, waiting, title !== null, elapsed(tool.startedAt, now))}
        </span>
        <OpenDelegated tool={tool} />
      </summary>
      <div className="mt-2 rounded-lg border border-border bg-subtle">
        {input.length > 0 && (
          <Io label={t.tool.input}>
            {input.length === 1 ? (
              <Pre text={input[0]?.value ?? ''} />
            ) : (
              input.map(field => (
                <div className="grid gap-px" key={field.name}>
                  <span className="font-mono text-2xs text-muted-foreground">{field.name}</span>
                  <Pre text={field.value} />
                </div>
              ))
            )}
          </Io>
        )}
        <Io failed={tool.status === 'failed'} label={t.tool.output}>
          <Output tool={tool} />
        </Io>
      </div>
    </details>
  )
}

/**
 * A task Atlas handed to an outside executor (step 16f): its `delegate` line opens the session in
 * the executors tab, as a subagent's line opens the subagent. It replaces the workbench's
 * cards.
 */
function OpenDelegated({ tool }: { tool: Tool }) {
  const conversationId = useStore($selected)
  const ids = delegatedTo(tool)

  if (!conversationId || ids.length === 0) {
    return null
  }

  return (
    <>
      {ids.map(id => (
        <button
          className="flex-none rounded border border-waiting/40 px-2 text-xs text-waiting-text"
          data-testid="open-delegated"
          key={id}
          onClick={event => {
            // A button in the line opens the session, not the line.
            event.preventDefault()
            void openDelegated(conversationId, id)
          }}
          title={t.executors.openTitle}
          type="button"
        >
          {t.executors.open}
        </button>
      ))}
    </>
  )
}

/** The subagents a `delegate` call gave an outside executor, from its result. */
export function delegatedTo(tool: Pick<Tool, 'args' | 'name' | 'result'>): string[] {
  if (tool.name !== 'delegate') {
    return []
  }

  try {
    const args: unknown = JSON.parse(tool.args || '{}')
    const result: unknown = JSON.parse(tool.result || '{}')
    const executor = args && typeof args === 'object' && 'executor' in args ? args.executor : null
    const ids = result && typeof result === 'object' && 'subagent_ids' in result ? result.subagent_ids : null

    return typeof executor === 'string' && executor && Array.isArray(ids)
      ? ids.filter((id): id is string => typeof id === 'string')
      : []
  } catch {
    return []
  }
}

const STATE_COLOR: Record<Shown, string> = {
  cancelled: 'text-muted-foreground',
  completed: 'text-muted-foreground',
  denied: 'text-muted-foreground',
  failed: 'text-failed-text',
  running: 'text-working',
  unanswered: 'text-muted-foreground',
  waiting: 'text-waiting-text'
}

/** How the call stands, beside its title; `titled`, the title already says it runs. */
function stateText(
  tool: Tool,
  shown: Shown,
  waiting: undefined | Waiting,
  titled: boolean,
  running: null | number
): string {
  const time = (seconds: null | number) => (seconds === null ? '' : t.duration(seconds))

  switch (shown) {
    case 'waiting':
      return t.tool.waitingFor[waiting ?? 'approval']
    case 'running': {
      const timer = running === null ? '' : t.elapsed(Math.floor(running))

      return titled ? timer : [t.tool.running, timer].filter(Boolean).join(' ')
    }
    case 'completed':
      return time(tool.seconds)
    case 'failed':
      return [t.tool.failed, time(tool.seconds)].filter(Boolean).join(' · ')
    case 'denied':
      return t.tool.denied
    case 'cancelled':
      return t.tool.cancelled
    case 'unanswered':
      return t.tool.unanswered
  }
}

function Io({ children, failed = false, label }: { children: ReactNode; failed?: boolean; label: string }) {
  return (
    <div className="grid grid-cols-[2.6em_minmax(0,1fr)] gap-2.5 px-3 py-2 [&+&]:border-t [&+&]:border-border">
      <span
        className={`pt-px font-mono text-2xs tracking-wider ${failed ? 'text-failed-text' : 'text-muted-foreground'}`}
      >
        {label}
      </span>
      <div className="grid min-w-0 gap-1.5">{children}</div>
    </div>
  )
}

function Pre({ failed = false, text }: { failed?: boolean; text: string }) {
  return (
    <pre
      className={`max-h-64 overflow-auto font-mono text-xs break-words whitespace-pre-wrap ${failed ? 'text-failed-text' : ''}`}
    >
      {text}
    </pre>
  )
}

function Output({ tool }: { tool: Tool }) {
  const [copied, setCopied] = useState(false)
  const text = tool.status === 'running' ? tool.progress.join('\n') : tool.result

  if (!text) {
    return <p className="text-xs text-muted-foreground italic">{tool.status === 'running' ? t.tool.waiting : ''}</p>
  }

  const shown = clampForDisplay(text)

  return (
    <>
      <Pre failed={tool.status === 'failed'} text={shown} />
      {shown !== text && (
        <button
          className="justify-self-start rounded border border-border-strong px-2 py-0.5 text-xs"
          onClick={() => {
            void navigator.clipboard.writeText(text).then(() => {
              setCopied(true)
            })
          }}
          type="button"
        >
          {copied ? t.tool.copied : t.tool.copyAll}
        </button>
      )}
      {tool.spillPath && <p className="text-xs text-muted-foreground">{t.tool.spilled(tool.spillPath)}</p>}
    </>
  )
}

/** The output on screen, cut at `MAX_SHOWN_CHARS` with what was left out. */
export function clampForDisplay(value: string, max = MAX_SHOWN_CHARS): string {
  if (value.length <= max) {
    return value
  }

  return `${value.slice(0, max)}\n\n${t.tool.clamped((value.length - max).toLocaleString())}`
}

/** The arguments for IN: one bare, several each under its name. */
export function inputFields(args: string): { name: string; value: string }[] {
  try {
    const parsed: unknown = JSON.parse(args || '{}')

    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return Object.entries(parsed).map(([name, value]) => ({
        name,
        value: typeof value === 'string' ? value : JSON.stringify(value, null, 2)
      }))
    }
  } catch {
    // Not JSON: shown as it came.
  }

  return args ? [{ name: '', value: args }] : []
}

type Titled = keyof typeof t.tool.titles

/** Atlas's own tools' titles (`Ran <command>`, `Read <path>`, `Searched “x”`), the verb saying
 * what the call did as it stands: done once it ran, pending while it runs, and plain (`Run
 * <command>`) while it waits or when it was denied or stopped first; `null` for the others, MCP's
 * and plugins' included, which keep the generic summary. */
export function toolTitle(name: string, args: string, shown: Shown): null | { action: string; target: string } {
  const field: Record<Titled, string> = { read_file: 'path', run_command: 'command', web_search: 'query' }

  if (!(name in field)) {
    return null
  }

  const titled = name as Titled
  const value = inputFields(args).find(entry => entry.name === field[titled])?.value

  if (!value) {
    return null
  }

  const words = t.tool.titles[titled]
  const target = titled === 'web_search' ? `“${value}”` : (value.split('\n')[0] ?? value)
  const action =
    shown === 'completed' || shown === 'failed' ? words.done : shown === 'running' ? words.pending : words.plain

  return { action, target }
}

/** The arguments as one short line: the values, not the JSON's braces and keys. */
export function summarize(args: string): string {
  try {
    const parsed: unknown = JSON.parse(args)

    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const values = Object.values(parsed).map(value => (typeof value === 'string' ? value : JSON.stringify(value)))

      return values.join(' · ').slice(0, 200)
    }
  } catch {
    // Not JSON: show it as it came.
  }

  return args.slice(0, 200)
}
