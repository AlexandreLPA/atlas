import { useState } from 'react'

import { t } from '@/i18n'
import type { ExecutorApprovalParams, ExecutorQuestionParams } from '@/protocol/protocol.generated'
import { answerExecutor, type Card } from '@/store/app'

export type ExecutorCard = Extract<Card, { kind: 'executor-approval' | 'executor-question' }>

/**
 * What an executor asks the user, inside its session's conversation as Atlas's own questions and
 * approvals are in Atlas's (step 16f; the cards of step 13, which the workbench of step 16c held).
 * It waits with no deadline. Styled apart from Atlas's: Anthropic's logo is left out, its trademark
 * guidelines asking for approval first, so the card carries the name.
 */
export function ExecutorCardView({ card }: { card: ExecutorCard }) {
  return card.kind === 'executor-approval' ? (
    <ApprovalCard id={card.id} params={card.params} />
  ) : (
    <QuestionCard id={card.id} params={card.params} />
  )
}

const CARD = 'space-y-2 rounded-xl border-l-4 border-orange-500 bg-white p-3 text-sm shadow-md dark:bg-neutral-950'

function ApprovalCard({ id, params }: { id: string; params: ExecutorApprovalParams }) {
  const [reason, setReason] = useState('')

  return (
    <div className={CARD} data-testid="executor-approval-card">
      <p className="font-semibold">{t.executor.wantsTo(params.executor_title, params.tool)}</p>
      <pre className="overflow-x-auto rounded bg-neutral-100 px-2 py-1 text-xs whitespace-pre-wrap dark:bg-neutral-900">
        {params.summary}
      </pre>
      {params.description && <p className="text-xs text-neutral-500">{params.description}</p>}
      <input
        aria-label={t.executor.reason}
        className="w-full rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-900"
        onChange={event => {
          setReason(event.target.value)
        }}
        placeholder={t.executor.reason}
        value={reason}
      />
      <div className="flex gap-2">
        <button
          className="rounded bg-orange-600 px-3 py-1 text-xs font-medium text-white"
          onClick={() => {
            answerExecutor(id, { allow: true })
          }}
          type="button"
        >
          {t.executor.allow}
        </button>
        <button
          className="rounded border border-neutral-300 px-3 py-1 text-xs dark:border-neutral-700"
          onClick={() => {
            answerExecutor(id, reason.trim() ? { allow: false, reason: reason.trim() } : { allow: false })
          }}
          type="button"
        >
          {t.executor.deny}
        </button>
      </div>
    </div>
  )
}

function QuestionCard({ id, params }: { id: string; params: ExecutorQuestionParams }) {
  const [picked, setPicked] = useState<Record<string, string[]>>({})
  const [other, setOther] = useState<Record<string, string>>({})

  const toggle = (question: string, label: string, multi: boolean) => {
    const current = picked[question] ?? []
    const next = multi
      ? current.includes(label)
        ? current.filter(item => item !== label)
        : [...current, label]
      : [label]
    setPicked({ ...picked, [question]: next })
  }

  const answers: Record<string, string | string[]> = {}

  for (const question of params.questions) {
    const typed = other[question.question]?.trim()
    const chosen = picked[question.question] ?? []

    if (typed) {
      answers[question.question] = typed
    } else if (chosen.length) {
      answers[question.question] = question.multi_select ? chosen : (chosen[0] ?? '')
    }
  }

  const complete = params.questions.every(question => question.question in answers)

  return (
    <div className={CARD} data-testid="executor-question-card">
      <p className="font-semibold">{t.executor.asks(params.executor_title)}</p>
      {params.questions.map(question => (
        <div className="space-y-1" key={question.question}>
          <p>
            {question.header && <span className="mr-1 text-xs text-orange-700">{question.header}</span>}
            {question.question}
          </p>
          <div className="flex flex-wrap gap-1">
            {question.choices.map(choice => (
              <button
                aria-pressed={(picked[question.question] ?? []).includes(choice.label)}
                className={`rounded border px-2 py-0.5 text-xs ${
                  (picked[question.question] ?? []).includes(choice.label)
                    ? 'border-orange-500 bg-orange-50 dark:bg-orange-950'
                    : 'border-neutral-300 dark:border-neutral-700'
                }`}
                key={choice.label}
                onClick={() => {
                  toggle(question.question, choice.label, question.multi_select)
                }}
                title={choice.description}
                type="button"
              >
                {choice.label}
              </button>
            ))}
          </div>
          <input
            aria-label={`${t.executor.other}: ${question.question}`}
            className="w-full rounded border border-neutral-300 px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-900"
            onChange={event => {
              setOther({ ...other, [question.question]: event.target.value })
            }}
            placeholder={t.executor.other}
            value={other[question.question] ?? ''}
          />
        </div>
      ))}
      <div className="flex gap-2">
        <button
          className="rounded bg-orange-600 px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
          disabled={!complete}
          onClick={() => {
            answerExecutor(id, { answers })
          }}
          type="button"
        >
          {t.executor.answer}
        </button>
        <button
          className="rounded border border-neutral-300 px-3 py-1 text-xs dark:border-neutral-700"
          onClick={() => {
            answerExecutor(id, {})
          }}
          type="button"
        >
          {t.executor.dismiss}
        </button>
      </div>
    </div>
  )
}
