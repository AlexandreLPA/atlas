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
            <span className={title.prose ? 'text-soft' : 'font-mono text-soft'}>{title.target}</span>
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

/** A call's title: the verb as the call stands, what it was about, and whether that is words (a
 * question, a goal, a browser's step) rather than a path or a command. */
export interface ToolTitle {
  action: string
  prose: boolean
  target: string
}

type Args = Record<string, unknown>

/** What names one call of Atlas's own tools: the key of its verbs in `t.tool.titles`, and its
 * target. */
interface Named {
  key: string
  prose?: boolean
  target: string
}

/** A field's first line, trimmed; empty when it is no text. */
function line(value: unknown): string {
  return typeof value === 'string' ? (value.split('\n')[0] ?? '').trim() : ''
}

/** A list field's first item, as an object; empty when there is none. */
function first(value: unknown): Args {
  const item: unknown = Array.isArray(value) ? value[0] : undefined

  return item && typeof item === 'object' ? (item as Args) : {}
}

/** `target`, and how many more of `list` there are. */
function andMore(target: string, list: unknown): string {
  const more = Array.isArray(list) ? list.length - 1 : 0

  return target && more > 0 ? t.tool.andMore(target, more) : target
}

const quoted = (value: string) => (value ? `“${value}”` : '')

/** The titles that say all with no target: the others, with none, keep the generic line. */
const ALONE = new Set([
  'cronjob_manage.list',
  'delegate.list',
  'desktop_preview.close',
  'executor_accounts',
  'plan',
  'plan.read',
  'secrets.list'
])

/** Atlas's own tools, each naming its call from its arguments (`null`: they name nothing). */
const NAMED: Record<string, (args: Args) => Named | null> = {
  ask_user: args => ({
    key: 'ask_user',
    prose: true,
    target: andMore(line(first(args.questions).question), args.questions)
  }),
  bot_create: args => ({ key: 'bot_create', target: line(args.title) || line(args.name) }),
  // Its code starts with a one-line comment saying what the step does.
  browser_exec: args => {
    const said = line(args.code)

    return { key: 'browser_exec', prose: true, target: said.startsWith('#') ? said.replace(/^#+\s*/, '') : '' }
  },
  conversation_search: args => ({ key: 'conversation_search', target: quoted(line(args.query)) }),
  cronjob_manage: args => {
    const action = line(args.action)

    return {
      key: `cronjob_manage.${action}`,
      target: action === 'create' ? line(args.name) || line(args.schedule) : line(args.job_id)
    }
  },
  delegate: args => {
    const action = line(args.action) || 'spawn'

    return action === 'spawn'
      ? { key: 'delegate.spawn', prose: true, target: andMore(line(first(args.tasks).goal), args.tasks) }
      : { key: `delegate.${action}`, target: line(args.subagent_id) || line(args.account) }
  },
  delegation_search: args => ({ key: 'delegation_search', target: quoted(line(args.query)) }),
  desktop_preview: args =>
    line(args.action) === 'close'
      ? { key: 'desktop_preview.close', target: line(args.label) || line(args.url) }
      : { key: 'desktop_preview.open', target: line(args.label) || line(args.url) },
  edit_file: args => (line(args.path) ? { key: 'edit_file', target: line(args.path) } : null),
  executor_accounts: args => ({ key: 'executor_accounts', target: line(args.executor) }),
  find_files: args => ({ key: 'find_files', target: quoted(line(args.pattern)) }),
  memory: args => {
    const change = Array.isArray(args.operations) ? first(args.operations) : args

    return {
      key: line(args.target) === 'user' ? 'memory.user' : 'memory.memory',
      prose: true,
      target: line(change.content) || line(change.new_text) || line(change.old_text)
    }
  },
  message_agent: args => ({ key: 'message_agent', target: line(args.target) }),
  plan: args => {
    const todos = Array.isArray(args.todos) ? (args.todos as unknown[]) : []
    const doing = todos.find(todo => (todo as Args | null)?.status === 'in_progress') as Args | undefined

    return todos.length ? { key: 'plan', prose: true, target: line(doing?.content) } : { key: 'plan.read', target: '' }
  },
  read_file: args => (line(args.path) ? { key: 'read_file', target: line(args.path) } : null),
  run_command: args =>
    line(args.stop)
      ? { key: 'run_command.stop', target: line(args.stop) }
      : line(args.command)
        ? { key: 'run_command', target: line(args.command) }
        : null,
  secrets: args =>
    line(args.action) === 'list'
      ? { key: 'secrets.list', target: '' }
      : { key: 'secrets.request', target: line(args.name) },
  skill_manage: args => {
    const change = Array.isArray(args.operations) ? first(args.operations) : args
    const action = line(change.action)
    const key = action === 'create' || action === 'delete' ? `skill_manage.${action}` : 'skill_manage.change'

    return { key, target: line(change.name) }
  },
  tool_search: args => ({ key: 'tool_search', target: quoted(line(args.query)) }),
  use_skill: args => ({ key: 'use_skill', target: line(args.name) }),
  web_fetch: args => (line(args.url) ? { key: 'web_fetch', target: line(args.url) } : null),
  web_search: args => (line(args.query) ? { key: 'web_search', target: quoted(line(args.query)) } : null),
  write_file: args => (line(args.path) ? { key: 'write_file', target: line(args.path) } : null)
}

/** Atlas's own tools' titles (`Ran <command>`, `Asked “Which colour?”`, `Delegated: <goal>`), the
 * verb saying what the call did as it stands: done once it ran, pending while it runs, and plain
 * (`Run <command>`) while it waits or when it was denied or stopped first; `null` for the others,
 * MCP's and plugins' included, which keep the generic summary, and for a call whose arguments
 * name nothing. */
export function toolTitle(name: string, args: string, shown: Shown): null | ToolTitle {
  const parsed = parsedArgs(args)
  const named = parsed && Object.hasOwn(NAMED, name) ? NAMED[name]?.(parsed) : null
  const words = named ? t.tool.titles[named.key] : undefined

  if (!named || !words || (!named.target && !ALONE.has(named.key))) {
    return null
  }

  const action =
    shown === 'completed' || shown === 'failed' ? words.done : shown === 'running' ? words.pending : words.plain

  return { action, prose: named.prose ?? false, target: named.target }
}

/** The arguments as an object, when they are one. */
function parsedArgs(args: string): Args | null {
  try {
    const parsed: unknown = JSON.parse(args || '{}')

    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Args) : null
  } catch {
    return null
  }
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
