import { useState } from 'react'

import { t } from '@/i18n'
import type { QuestionRow } from '@/protocol/protocol.generated'
import { answerAsk, type Card } from '@/store/app'

import { CardAnswering } from './card-answering'
import { AHEAD, CardFrame, Choice, Choices, FIELD, OUT } from './card-frame'

type AskCardProps = { card: Extract<Card, { kind: 'ask' }> }

/** The model's questions as a card: 1 to 5 questions, each with its choices (the recommended
 * first, each under its number key), "Other" with typed text, several when the question allows, or
 * skip. */
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
    <CardFrame testId="ask-card" title={t.ask.title(questions.length)}>
      {questions.map(question => (
        <fieldset className="space-y-2" key={question.id}>
          <legend className="mb-1">{question.text}</legend>
          {question.multi_select && <p className="text-xs text-muted-foreground">{t.ask.pickSome}</p>}
          {question.choices.length > 0 && (
            <Choices>
              {[...question.choices, OTHER].map((choice, index) => (
                <Choice
                  index={index}
                  key={choice}
                  label={choice === OTHER ? t.ask.other : choice}
                  onClick={() => {
                    toggle(question, choice)
                  }}
                  pressed={(picked[question.id] ?? []).includes(choice)}
                />
              ))}
            </Choices>
          )}
          {(question.choices.length === 0 || (picked[question.id] ?? []).includes(OTHER)) && (
            <input
              aria-label={`${question.text} ${t.ask.otherPlaceholder}`}
              className={FIELD}
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
        <button className={AHEAD} onClick={submit} type="button">
          {t.ask.submit}
        </button>
        <button
          className={OUT}
          onClick={() => {
            answerAsk(card.id, {})
          }}
          type="button"
        >
          {t.ask.skip}
        </button>
      </div>
    </CardFrame>
  )
}

/** The screen's "Other" choice; never a choice the agent wrote, since it is not text. */
const OTHER = '\u0000other'
