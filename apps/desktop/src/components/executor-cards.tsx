import { useState } from 'react'

import { t } from '@/i18n'
import type { ExecutorApprovalParams, ExecutorQuestionParams } from '@/protocol/protocol.generated'
import { answerExecutor, type Card } from '@/store/app'

import { CardAnswering } from './card-answering'
import { AHEAD, CardFrame, Choice, Choices, FIELD, OUT, Target } from './card-frame'

export type ExecutorCard = Extract<Card, { kind: 'executor-approval' | 'executor-question' }>

/**
 * What an executor asks the user, inside its session's conversation as Atlas's own questions and
 * approvals are in Atlas's (step 16f; the cards of step 13, which the workbench of step 16c held),
 * and drawn as they are. It waits with no deadline. Its title names the executor: Anthropic's logo
 * is left out, its trademark guidelines asking for approval first.
 */
export function ExecutorCardView({ card }: { card: ExecutorCard }) {
  if (card.answered) {
    return card.kind === 'executor-approval' ? (
      <CardAnswering
        testId="executor-approval-card"
        title={t.executor.wantsTo(card.params.executor_title, card.params.tool)}
      />
    ) : (
      <CardAnswering testId="executor-question-card" title={t.executor.asks(card.params.executor_title)} />
    )
  }

  return card.kind === 'executor-approval' ? (
    <ApprovalCard id={card.id} params={card.params} />
  ) : (
    <QuestionCard id={card.id} params={card.params} />
  )
}

function ApprovalCard({ id, params }: { id: string; params: ExecutorApprovalParams }) {
  const [reason, setReason] = useState('')

  return (
    <CardFrame testId="executor-approval-card" title={t.executor.wantsTo(params.executor_title, params.tool)}>
      <Target>{params.summary}</Target>
      {params.description && <p className="text-soft">{params.description}</p>}
      <Choices>
        <Choice
          index={0}
          label={t.executor.allow}
          onClick={() => {
            answerExecutor(id, { allow: true })
          }}
          primary
        />
        <Choice
          index={1}
          label={t.executor.deny}
          onClick={() => {
            answerExecutor(id, reason.trim() ? { allow: false, reason: reason.trim() } : { allow: false })
          }}
        />
      </Choices>
      <input
        aria-label={t.executor.reason}
        className={FIELD}
        onChange={event => {
          setReason(event.target.value)
        }}
        placeholder={t.executor.reason}
        value={reason}
      />
    </CardFrame>
  )
}

/** Its questions as Atlas's own are asked: each with its choices under their number keys, "Other"
 * with typed text, several where the question allows; answered once each has an answer. */
function QuestionCard({ id, params }: { id: string; params: ExecutorQuestionParams }) {
  const [picked, setPicked] = useState<Record<string, string[]>>({})
  const [typed, setTyped] = useState<Record<string, string>>({})

  const toggle = (question: string, label: string, multi: boolean) => {
    setPicked(current => {
      const now = current[question] ?? []
      const next = multi ? (now.includes(label) ? now.filter(item => item !== label) : [...now, label]) : [label]

      return { ...current, [question]: next }
    })
  }

  const answers: Record<string, string | string[]> = {}

  for (const question of params.questions) {
    const chosen = (picked[question.question] ?? []).filter(label => label !== OTHER)
    const other = (picked[question.question] ?? []).includes(OTHER) || question.choices.length === 0
    const text = other ? (typed[question.question] ?? '').trim() : ''
    const all = text ? [...chosen, text] : chosen

    if (all.length) {
      answers[question.question] = question.multi_select ? all : (all[0] ?? '')
    }
  }

  const complete = params.questions.every(question => question.question in answers)

  return (
    <CardFrame testId="executor-question-card" title={t.executor.asks(params.executor_title)}>
      {params.questions.map(question => (
        <fieldset className="space-y-2" key={question.question}>
          <legend className="mb-1">
            {question.header && <span className="mr-1.5 text-xs text-waiting-text">{question.header}</span>}
            {question.question}
          </legend>
          {question.choices.length > 0 && (
            <Choices>
              {[...question.choices, { description: '', label: OTHER }].map((choice, index) => (
                <Choice
                  index={index}
                  key={choice.label}
                  label={choice.label === OTHER ? t.executor.other : choice.label}
                  onClick={() => {
                    toggle(question.question, choice.label, question.multi_select)
                  }}
                  pressed={(picked[question.question] ?? []).includes(choice.label)}
                  title={choice.description || undefined}
                />
              ))}
            </Choices>
          )}
          {(question.choices.length === 0 || (picked[question.question] ?? []).includes(OTHER)) && (
            <input
              aria-label={`${t.executor.other}: ${question.question}`}
              className={FIELD}
              onChange={event => {
                setTyped(current => ({ ...current, [question.question]: event.target.value }))
              }}
              placeholder={t.executor.other}
              value={typed[question.question] ?? ''}
            />
          )}
        </fieldset>
      ))}
      <div className="flex gap-2">
        <button
          className={AHEAD}
          disabled={!complete}
          onClick={() => {
            answerExecutor(id, { answers })
          }}
          type="button"
        >
          {t.executor.answer}
        </button>
        <button
          className={OUT}
          onClick={() => {
            answerExecutor(id, {})
          }}
          type="button"
        >
          {t.executor.skip}
        </button>
      </div>
    </CardFrame>
  )
}

/** The screen's "Other" choice; never a label the executor wrote, since it is not text. */
const OTHER = '\u0000other'
