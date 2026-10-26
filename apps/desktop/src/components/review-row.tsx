import { Fragment, useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import { showProposals } from '@/store/app'
import type { Item } from '@/store/thread'

type Review = Extract<Item, { kind: 'review' }>

/**
 * The background review's summary, one line. An action that changed a skill opens its skill
 * diff in a click, one at a time, coloured as an inline diff.
 */
export function ReviewRow({ review }: { review: Review }) {
  const [open, setOpen] = useState<null | string>(null)
  const diff = open === null ? undefined : review.diffs[open]
  const shown = useRef<HTMLPreElement>(null)

  // The summary is the conversation's last line, so its diff opens below the edge: bring it in.
  useEffect(() => {
    shown.current?.scrollIntoView({ block: 'nearest' })
  }, [open])

  return (
    <div className="space-y-2">
      <p className="text-center text-xs text-muted-foreground" data-testid="review-summary">
        {review.actions.map((action, index) => (
          <Fragment key={action}>
            {index > 0 && ' · '}
            {review.diffs[action] === undefined ? (
              action
            ) : (
              <button
                aria-expanded={open === action}
                className="underline decoration-dotted underline-offset-2 hover:text-foreground"
                onClick={() => {
                  setOpen(open === action ? null : action)
                }}
                type="button"
              >
                {action}
              </button>
            )}
          </Fragment>
        ))}
        {review.proposals.length > 0 && (
          <button className="link ml-2 font-medium" onClick={showProposals} type="button">
            {t.proposals.review}
          </button>
        )}
      </p>
      {diff !== undefined && (
        <pre
          className="max-h-96 overflow-auto rounded-md border border-border py-1 font-mono text-xs whitespace-pre-wrap"
          data-testid="skill-diff"
          ref={shown}
        >
          {diffRows(diff).map((row, index) => (
            <DiffLine key={index} row={row} />
          ))}
        </pre>
      )}
    </div>
  )
}

const LINE = {
  file: 'mt-1 font-semibold text-soft first:mt-0',
  gap: 'text-muted-foreground',
  minus: 'bg-removed/10',
  note: 'text-muted-foreground italic',
  plus: 'bg-added/10',
  same: 'text-muted-foreground'
} as const

const MARK = {
  minus: 'bg-removed/30',
  plus: 'bg-added/30'
} as const

function DiffLine({ row }: { row: DiffRow }) {
  const sign = row.kind === 'minus' ? '-' : row.kind === 'plus' ? '+' : ' '
  const [start, end] = row.changed ?? [0, 0]

  return (
    <div className={`px-3 ${LINE[row.kind]}`} data-kind={row.kind}>
      {(row.kind === 'minus' || row.kind === 'plus' || row.kind === 'same') && (
        <span className="select-none">{sign} </span>
      )}
      {start < end && (row.kind === 'minus' || row.kind === 'plus') ? (
        <>
          {row.text.slice(0, start)}
          <mark className={`rounded-sm text-inherit ${MARK[row.kind]}`}>{row.text.slice(start, end)}</mark>
          {row.text.slice(end)}
        </>
      ) : (
        row.text
      )}
    </div>
  )
}

export interface DiffRow {
  /** Where the line differs from its pair on the other side: the part to mark. */
  changed?: [number, number]
  kind: 'file' | 'gap' | 'minus' | 'note' | 'plus' | 'same'
  text: string
}

/**
 * A skill diff as rows: a file's two header lines become its path, a hunk header becomes a gap
 * between two hunks of a file, and a removed line followed by its replacement marks only the words
 * that changed, since a skill's line is a whole paragraph.
 */
export function diffRows(diff: string): DiffRow[] {
  const rows: DiffRow[] = []
  let from = ''
  let hunks = 0

  for (const line of diff.split('\n')) {
    if (line.startsWith('--- ')) {
      from = line.slice(4)
    } else if (line.startsWith('+++ ')) {
      const to = line.slice(4)

      rows.push({ kind: 'file', text: (to === '/dev/null' ? from : to).replace(/^[ab]\//, '') })
      hunks = 0
    } else if (line.startsWith('@@')) {
      hunks += 1

      if (hunks > 1) {
        rows.push({ kind: 'gap', text: '⋯' })
      }
    } else if (line.startsWith('-')) {
      rows.push({ kind: 'minus', text: line.slice(1) })
    } else if (line.startsWith('+')) {
      rows.push({ kind: 'plus', text: line.slice(1) })
    } else if (line.startsWith(' ')) {
      rows.push({ kind: 'same', text: line.slice(1) })
    } else if (line) {
      rows.push({ kind: 'note', text: line })
    }
  }

  return markPairs(rows)
}

/** A run of removed lines followed by as many added lines is the same lines rewritten. */
function markPairs(rows: DiffRow[]): DiffRow[] {
  const marked = [...rows]
  let index = 0

  while (index < marked.length) {
    let minus = index

    while (marked[minus]?.kind === 'minus') {
      minus += 1
    }

    let plus = minus

    while (marked[plus]?.kind === 'plus') {
      plus += 1
    }

    const count = minus - index

    if (count > 0 && plus - minus === count) {
      for (let pair = 0; pair < count; pair += 1) {
        const before = marked[index + pair] as DiffRow
        const after = marked[minus + pair] as DiffRow
        const [prefix, suffix] = shared(before.text, after.text)

        if (prefix + suffix > 0) {
          marked[index + pair] = { ...before, changed: [prefix, before.text.length - suffix] }
          marked[minus + pair] = { ...after, changed: [prefix, after.text.length - suffix] }
        }
      }
    }

    index = Math.max(plus, index + 1)
  }

  return marked
}

/** How many characters two lines share at the start and at the end, in whole words. */
function shared(before: string, after: string): [number, number] {
  const most = Math.min(before.length, after.length)
  let prefix = 0

  while (prefix < most && before[prefix] === after[prefix]) {
    prefix += 1
  }

  // Back to the start of the word the difference falls in.
  while (prefix > 0 && (midWord(before, prefix) || midWord(after, prefix))) {
    prefix -= 1
  }

  let suffix = 0

  while (suffix < most - prefix && before.at(-1 - suffix) === after.at(-1 - suffix)) {
    suffix += 1
  }

  // And on to the end of the word it ends in.
  while (suffix > 0 && (midWord(before, before.length - suffix) || midWord(after, after.length - suffix))) {
    suffix -= 1
  }

  return [prefix, suffix]
}

function midWord(text: string, at: number): boolean {
  return /\S/.test(text.charAt(at - 1)) && /\S/.test(text.charAt(at))
}
