import type { KeyboardEvent, ReactNode } from 'react'

/**
 * The look every card that waits for the user shares, Atlas's and an executor's (the UI review,
 * 26/10/2026): amber, its question as the title, then what it is about and its choices, the one
 * that goes ahead first and the one that refuses last. While the keyboard is in the card, a number
 * key picks the choice it labels: in the row that holds the keyboard, or else the card's first. A
 * click on the card gives it the keyboard; the card never takes it by itself, so what the user
 * types elsewhere stays theirs.
 */
export function CardFrame({ children, testId, title }: { children: ReactNode; testId: string; title: string }) {
  return (
    <section
      aria-label={title}
      className="enter space-y-3 rounded-lg border border-waiting/40 bg-waiting/10 p-4 text-sm"
      data-testid={testId}
      onKeyDown={pickByNumber}
      tabIndex={-1}
    >
      <h2 className="font-semibold">{title}</h2>
      {children}
    </section>
  )
}

/** Exactly what would run or change, whole: the command, the file, the call's arguments. */
export function Target({ children }: { children: string }) {
  return (
    <pre
      className="max-h-48 overflow-auto rounded-md border border-border bg-raised px-3 py-2 font-mono text-xs break-words whitespace-pre-wrap"
      data-testid="card-target"
    >
      {children}
    </pre>
  )
}

/** A row of choices, numbered in order for the keyboard. */
export function Choices({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap gap-2" data-choices="">
      {children}
    </div>
  )
}

type ChoiceProps = {
  /** Its place in its row, from 0; the key that picks it is the next number, up to 9. */
  index: number
  label: string
  onClick: () => void
  /** One of a question's answers, which is chosen or not. */
  pressed?: boolean
  /** The row's way ahead, filled. */
  primary?: boolean
  title?: string
}

export function Choice({ index, label, onClick, pressed, primary = false, title }: ChoiceProps) {
  const key = index < 9 ? String(index + 1) : undefined
  const filled = primary || pressed === true

  return (
    <button
      aria-keyshortcuts={key}
      aria-pressed={pressed}
      className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 ${pressed === undefined ? 'font-medium' : ''} ${
        filled ? 'border-primary bg-primary text-primary-foreground' : 'border-border-strong bg-raised hover:bg-muted'
      }`}
      data-choice=""
      onClick={onClick}
      title={title}
      type="button"
    >
      {key && (
        <kbd aria-hidden className="font-sans text-2xs font-normal tabular-nums opacity-60">
          {key}
        </kbd>
      )}
      {label}
    </button>
  )
}

/** A card's way ahead once its fields are filled (Answer, Save), and its way out (Skip). */
export const AHEAD =
  'rounded-md border border-primary bg-primary px-3 py-1.5 font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50'
export const OUT = 'rounded-md px-3 py-1.5 text-soft hover:bg-selected'

/** A field the card asks for: a reason, an answer of the user's own, a key. */
export const FIELD = 'w-full rounded-md border border-border-strong bg-raised px-3 py-1.5'

function pickByNumber(event: KeyboardEvent<HTMLElement>) {
  const target = event.target as HTMLElement

  if (
    !/^[1-9]$/.test(event.key) ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    target.closest('input, textarea, [contenteditable="true"]')
  ) {
    return
  }

  const row = target.closest('[data-choices]') ?? event.currentTarget.querySelector('[data-choices]')
  const choice = row?.querySelectorAll<HTMLButtonElement>('[data-choice]')[Number(event.key) - 1]

  if (choice && !choice.disabled) {
    event.preventDefault()
    choice.focus()
    choice.click()
  }
}
