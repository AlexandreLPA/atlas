import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { Streamdown } from 'streamdown'

import { t } from '@/i18n'
import type { AttachmentRow } from '@/protocol/protocol.generated'
import { $remote, currentOwner } from '@/store/app'
import { openPreview, targetOf } from '@/store/previews'
import { isPart, type Item, type Part, reasoningAbout, type Thread } from '@/store/thread'

import { ARTIFACT_LANGUAGES, ArtifactBlock } from './artifact-block'
import { AttachmentChip, useThumbnail } from './attachment-chip'
import { ReviewRow } from './review-row'
import { AnswerStreaming, SHELL_RENDERERS } from './shell-block'
import { ToolRow } from './tool-row'
import { ArrowDown } from './ui/icons'
import { elapsed, useNow } from './use-now'

// A code block in a reply only copies: saving a command as `file.ps1` is no use.
const CONTROLS = { code: { copy: true, download: false } } as const
// A shell's block also runs, in the conversation's terminal (step 16f); a block
// of HTML, mermaid or Markdown opens beside, in a preview.
const ANSWER_PLUGINS = {
  renderers: [...SHELL_RENDERERS, { component: ArtifactBlock, language: ARTIFACT_LANGUAGES }]
}

// How close to the end, in pixels, still counts as being at it.
const NEAR_END = 48

/** `empty` is what an empty thread shows in place of the invitation: a bot's face and name. */
export function ThreadView({ empty, thread }: { empty?: ReactNode; thread: Thread }) {
  const scroller = useRef<HTMLDivElement>(null)
  const end = useRef<HTMLDivElement>(null)
  // The thread follows the reply only while the user is at its end: scrolled up to read what came
  // first, it stays still, and a button brings them back down (asked for by the user on 22/10/2026).
  const following = useRef(true)
  const lastTop = useRef(0)
  const [away, setAway] = useState(false)
  const last = thread.items.at(-1)
  const size = last ? growth(last) : 0
  const sent = last?.kind === 'user'

  useEffect(() => {
    // What the user sends, they want to see go out; at the end, the thread follows again.
    if (sent || following.current) {
      end.current?.scrollIntoView({ block: 'end' })
    }
  }, [thread.items.length, size, sent])

  function onScroll() {
    const element = scroller.current
    if (!element) {
      return
    }
    const near = element.scrollHeight - element.scrollTop - element.clientHeight <= NEAR_END
    // Only the user scrolls up: the thread growing, or following it, only moves it down.
    if (near) {
      following.current = true
    } else if (element.scrollTop < lastTop.current) {
      following.current = false
    }
    lastTop.current = element.scrollTop
    setAway(!near)
  }

  function toLatest() {
    following.current = true
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto" data-testid="thread" onScroll={onScroll} ref={scroller}>
        <AnswerStreaming.Provider value={thread.running}>
          <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
            {thread.items.length === 0 &&
              (empty ?? <p className="pt-24 text-center text-neutral-500">{t.thread.empty}</p>)}
            {segments(thread.items).map(segment =>
              Array.isArray(segment) ? (
                <Trail key={segment[0]?.key} parts={segment} />
              ) : (
                <ItemView item={segment} key={segment.key} />
              )
            )}
            {thread.running && <Working />}
            <div ref={end} />
          </div>
        </AnswerStreaming.Provider>
      </div>
      {away && (
        <button
          aria-label={t.thread.toLatest}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-neutral-200 bg-white p-2 text-neutral-600 shadow-md hover:text-neutral-900 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:text-neutral-100"
          data-testid="to-latest"
          onClick={toLatest}
          title={t.thread.toLatest}
          type="button"
        >
          <ArrowDown aria-hidden size={16} stroke={2} />
        </button>
      )}
    </div>
  )
}

/** The items with each run of consecutive parts gathered: one trail each. */
function segments(items: Item[]): (Exclude<Item, Part> | Part[])[] {
  const out: (Exclude<Item, Part> | Part[])[] = []

  for (const item of items) {
    const previous = out.at(-1)

    if (!isPart(item)) {
      out.push(item)
    } else if (Array.isArray(previous)) {
      previous.push(item)
    } else {
      out.push([item])
    }
  }

  return out
}

function growth(item: Item): number {
  switch (item.kind) {
    case 'reasoning':
    case 'text':
      return item.text.length
    case 'tool':
      return item.progress.length + item.result.length
    default:
      return 0
  }
}

/**
 * A turn's parts as a trail: a vertical line from the first part to
 * the last, one dot per part, its color the part's state.
 */
function Trail({ parts }: { parts: Part[] }) {
  return (
    <div className="space-y-3" data-testid="trail">
      {parts.map((part, index) => (
        <div
          className="relative min-w-0 pl-6"
          data-dot={dot(part)}
          data-kind={part.kind}
          data-testid="part"
          key={part.key}
        >
          {parts.length > 1 && (
            <span
              aria-hidden
              className={`absolute left-[6px] w-px bg-neutral-300 dark:bg-neutral-700 ${index === 0 ? 'top-[0.75em]' : '-top-3'} ${index === parts.length - 1 ? 'h-[calc(0.75em+0.75rem)]' : 'bottom-0'}`}
            />
          )}
          <span
            aria-hidden
            className={`absolute top-[calc(0.75em-4.5px)] left-[2px] size-[9px] rounded-full ${DOT[dot(part)]}`}
          />
          <PartView part={part} />
        </div>
      ))}
    </div>
  )
}

type Dot = 'denied' | 'failed' | 'final' | 'interim' | 'muted' | 'ok' | 'reasoning' | 'running'

const DOT: Record<Dot, string> = {
  denied: 'bg-neutral-400 dark:bg-neutral-600',
  failed: 'bg-red-600 dark:bg-red-500',
  final: 'bg-neutral-900 dark:bg-neutral-100',
  interim: 'bg-neutral-400 dark:bg-neutral-600',
  muted: 'bg-neutral-400 dark:bg-neutral-600',
  ok: 'bg-green-600 dark:bg-green-500',
  reasoning: 'border-[1.5px] border-neutral-400 bg-white dark:border-neutral-500 dark:bg-neutral-950',
  running: 'animate-pulse bg-blue-600 dark:bg-blue-400'
}

/** The dot's color is the part's state. */
function dot(part: Part): Dot {
  switch (part.kind) {
    case 'reasoning':
      return 'reasoning'
    case 'text':
      return part.role === 'interim' ? 'interim' : 'final'
    case 'other':
      return 'muted'
    case 'tool':
      switch (part.status) {
        case 'completed':
          return 'ok'
        case 'failed':
          return 'failed'
        case 'running':
          return 'running'
        case 'denied':
        case 'cancelled':
          return 'denied'
      }
  }
}

function PartView({ part }: { part: Part }) {
  switch (part.kind) {
    case 'reasoning':
      return <ReasoningPart part={part} />

    case 'text':
      // The interim text is lighter; the answer has the full weight. Nothing moves or goes away.
      return (
        <div
          className={part.role === 'interim' ? 'text-sm text-neutral-500 dark:text-neutral-400' : ''}
          data-role={part.role}
          data-testid="assistant-message"
        >
          <Streamdown
            controls={CONTROLS}
            isAnimating={part.streaming}
            mode={part.streaming ? 'streaming' : 'static'}
            plugins={ANSWER_PLUGINS}
          >
            {part.text}
          </Streamdown>
        </div>
      )

    case 'tool':
      return <ToolRow tool={part} />

    case 'other':
      // ACP v2: what the app does not know is kept raw and shown generically.
      return (
        <details className="text-sm text-neutral-500" data-testid="other-part">
          <summary className="cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden">
            <span className="font-mono">{part.type}</span> · {t.thread.other}
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto font-mono text-xs whitespace-pre-wrap">
            {JSON.stringify(part.payload, null, 2)}
          </pre>
        </details>
      )
  }
}

/** Reasoning in one closed line: "Thinking…" and its timer while it comes,
 * "Thought for 4 s" after, "Thought" reopened, and what it is about. Open, it is Markdown. */
function ReasoningPart({ part }: { part: Extract<Part, { kind: 'reasoning' }> }) {
  const now = useNow(part.streaming)
  const running = part.streaming ? elapsed(part.startedAt, now) : null
  // Under a second it thought briefly, and the time is whole seconds.
  const label = part.streaming
    ? [t.thread.thinking, running === null ? '' : t.elapsed(Math.floor(running))].filter(Boolean).join(' ')
    : part.seconds === null
      ? t.thread.thought
      : part.seconds < 1
        ? t.thread.thoughtBriefly
        : t.thread.thoughtFor(t.elapsed(Math.round(part.seconds)))
  const about = reasoningAbout(part.text)

  return (
    <details className="text-sm text-neutral-500 dark:text-neutral-400" data-testid="reasoning">
      <summary className="cursor-pointer list-none truncate select-none [&::-webkit-details-marker]:hidden">
        {label}
        {about && <span className="text-neutral-400 dark:text-neutral-500"> · {about}</span>}
      </summary>
      <div className="mt-1">
        {/* Streaming, as the answer streams: in static mode Streamdown sets its blocks from an effect
            on every delta, and a fast stream of them passed React's limit of nested updates. */}
        <Streamdown controls={CONTROLS} mode={part.streaming ? 'streaming' : 'static'}>
          {part.text}
        </Streamdown>
      </div>
    </details>
  )
}

function ItemView({ item }: { item: Exclude<Item, Part> }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className="flex flex-col items-end" data-testid="user-message">
          {item.attachments.length > 0 && (
            <div className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-2" data-testid="message-attachments">
              {item.attachments.map(attachment => (
                <SentAttachment attachment={attachment} key={attachment.id} />
              ))}
            </div>
          )}
          {item.text && (
            <div className="max-w-[85%] rounded-2xl bg-neutral-100 px-4 py-2 whitespace-pre-wrap dark:bg-neutral-800">
              {item.text}
            </div>
          )}
          {item.skill && (
            <span className="mt-1 text-xs text-neutral-500" data-skill={item.skill} data-testid="skill-invoked">
              {t.thread.skill}
            </span>
          )}
          {item.steered && <span className="mt-1 text-xs text-neutral-500">{t.thread.steered}</span>}
        </div>
      )

    case 'summary':
      return (
        <details className="rounded-md border border-dashed border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700">
          <summary className="cursor-pointer text-neutral-500">{t.thread.summary}</summary>
          <p className="mt-2 whitespace-pre-wrap">{item.text}</p>
        </details>
      )

    case 'status':
      return <p className="text-center text-xs text-neutral-500">{item.text}</p>

    case 'file':
      return (
        <div className="flex flex-col items-start gap-1" data-testid="file-sent">
          <span className="text-xs text-neutral-500">{t.thread.fileSent}</span>
          <SentAttachment attachment={item.attachment} />
        </div>
      )

    case 'notice':
      return (
        <details
          className="rounded-md border border-dashed border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700"
          data-testid="notice"
        >
          <summary className="cursor-pointer text-neutral-500">{t.thread.notice}</summary>
          <p className="mt-2 whitespace-pre-wrap">{item.text}</p>
        </details>
      )

    case 'review':
      return <ReviewRow review={item} />

    case 'terminal':
      return <TerminalRowView item={item} />

    case 'end':
      return (
        <p className="text-sm text-neutral-500" data-testid="turn-end">
          {endText(item)}
        </p>
      )
  }
}

/** A file the user sent (step 16g), as its chip was in the box; a click opens it beside, when the
 * server is on this machine. */
function SentAttachment({ attachment }: { attachment: AttachmentRow }) {
  const thumbnail = useThumbnail(attachment.path, attachment.kind)
  const local = !useStore($remote) && Boolean(attachment.path)

  return (
    <AttachmentChip
      detail={t.attachments.size(attachment.size)}
      kind={attachment.kind}
      name={attachment.name}
      onOpen={
        local
          ? () => {
              openPreview(currentOwner(), targetOf(attachment.path, attachment.name), true)
            }
          : undefined
      }
      thumbnail={thumbnail}
    />
  )
}

/** A command the user ran and what came of it (step 16f), drawn as a terminal's lines. */
function TerminalRowView({ item }: { item: Extract<Item, { kind: 'terminal' }> }) {
  const failed = item.exitCode !== null && item.exitCode !== 0

  return (
    <div className="flex flex-col items-end" data-testid="terminal-row">
      <div className="w-full max-w-[85%] overflow-hidden rounded-xl bg-neutral-900 font-mono text-xs text-neutral-100 dark:bg-black">
        <div className="border-b border-neutral-700 px-3 py-2 whitespace-pre-wrap">
          <span className="text-green-400">$</span> {item.command}
        </div>
        {item.output && (
          <pre
            className="max-h-64 overflow-auto px-3 py-2 break-words whitespace-pre-wrap"
            data-testid="terminal-output"
          >
            {item.output}
          </pre>
        )}
        {(item.exitCode !== null || item.state) && (
          <div className="flex gap-3 border-t border-neutral-700 px-3 py-1.5 text-[0.7rem] text-neutral-400">
            {item.exitCode !== null && (
              <span className={failed ? 'text-red-400' : ''} data-testid="terminal-exit">
                {t.terminal.exit(item.exitCode)}
              </span>
            )}
            {item.state && <span data-testid="terminal-state">{item.state}</span>}
          </div>
        )}
      </div>
      {item.steered && <span className="mt-1 text-xs text-neutral-500">{t.thread.steered}</span>}
    </div>
  )
}

function endText(item: Extract<Item, { kind: 'end' }>): string {
  switch (item.status) {
    case 'interrupted':
      return t.thread.interrupted
    case 'halted':
      return t.thread.halted
    case 'limited':
      return t.thread.limited
    case 'failed':
      // The server's words say why, and often what to do ("store it with atlas secrets set");
      // the kind of error alone does not (found in use, 15/10).
      return t.thread.failed(item.text || (item.error ?? ''))
  }
}

function Working() {
  return (
    <div aria-label="Working" className="flex gap-1 py-2" role="status">
      <span className="h-2 w-2 animate-pulse rounded-full bg-neutral-400" />
      <span className="h-2 w-2 animate-pulse rounded-full bg-neutral-400 [animation-delay:150ms]" />
      <span className="h-2 w-2 animate-pulse rounded-full bg-neutral-400 [animation-delay:300ms]" />
    </div>
  )
}
