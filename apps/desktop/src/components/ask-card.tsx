import { useState } from 'react'

import { t } from '@/i18n'
import type { QuestionRow } from '@/protocol/protocol.generated'
import { answerAsk, type Card } from '@/store/app'

import { CardAnswering } from './card-answering'

type AskCardProps = { card: Extract<Card, { kind: 'ask' }> }

/** The model's questions as a card: 1 to 5 questions, each with its choices
 * (the recommended first), "Other" with typed text, several when the question allows, or skip. */
export function AskCard({ card }: AskCardProps) {
  const questions = card.params.questions
  const [picked, setPicked] = useState<Record<string, string[]>>({})
  const [typed, setTyped] = useState<Record<string, string>>({})

  if (card.answered) {
    return <CardAnswering testId="ask-card" title={t.ask.title(questions.length)} />
  }

  const toggle = (question: QuestionRow, choice: string) => {
    setPicked(current => {
      const now = current[question.id] ?? []
      const next = question.multi_select
        ? now.includes(choice)
          ? now.filter(c => c !== choice)
          : [...now, choice]
        : [choice]

      return { ...current, [question.id]: next }
    })
  }

  const submit = () => {
    const answers: Record<string, null | string | string[]> = {}

    for (const question of questions) {
      const chosen = (picked[question.id] ?? []).filter(choice => choice !== OTHER)
      const other = (picked[question.id] ?? []).includes(OTHER) || question.choices.length === 0
      const text = other ? (typed[question.id] ?? '').trim() : ''
      const all = text ? [...chosen, text] : chosen

      answers[question.id] = question.multi_select ? all : (all[0] ?? null)
    }

    answerAsk(card.id, { answers })
  }

  return (
    <section
      aria-label={t.ask.title(questions.length)}
      className="enter space-y-4 rounded-lg border border-waiting/40 bg-waiting/10 p-4"
      data-testid="ask-card"
    >
      <h2 className="text-sm font-semibold">{t.ask.title(questions.length)}</h2>
      {questions.map(question => (
        <fieldset className="space-y-2" key={question.id}>
          <legend className="mb-1">{question.text}</legend>
          {question.multi_select && <p className="text-xs text-muted-foreground">{t.ask.pickSome}</p>}
          <div className="flex flex-wrap gap-2">
            {[...question.choices, ...(question.choices.length ? [OTHER] : [])].map(choice => {
              const on = (picked[question.id] ?? []).includes(choice)

              return (
                <button
                  aria-pressed={on}
                  className={`rounded-md border px-3 py-1 text-sm ${
                    on
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border-strong bg-raised hover:bg-muted'
                  }`}
                  key={choice}
                  onClick={() => {
                    toggle(question, choice)
                  }}
                  type="button"
                >
                  {choice === OTHER ? t.ask.other : choice}
                </button>
              )
            })}
          </div>
          {(question.choices.length === 0 || (picked[question.id] ?? []).includes(OTHER)) && (
            <input
              aria-label={`${question.text} ${t.ask.otherPlaceholder}`}
              className="w-full rounded-md border border-border-strong bg-raised px-3 py-1.5 text-sm"
              onChange={event => {
                setTyped(current => ({ ...current, [question.id]: event.target.value }))
              }}
              placeholder={t.ask.otherPlaceholder}
              value={typed[question.id] ?? ''}
            />
          )}
        </fieldset>
      ))}
      <div className="flex gap-2">
        <button
          className="rounded-md bg-primary px-4 py-1.5 text-sm font-medium text-primary-foreground"
          onClick={submit}
          type="button"
        >
          {t.ask.submit}
        </button>
        <button
          className="rounded-md px-4 py-1.5 text-sm text-soft hover:bg-selected"
          onClick={() => {
            answerAsk(card.id, {})
          }}
          type="button"
        >
          {t.ask.skip}
        </button>
      </div>
    </section>
  )
}

/** The screen's "Other" choice; never a choice the agent wrote, since it is not text. */
const OTHER = '\u0000other'
