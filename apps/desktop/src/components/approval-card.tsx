import { useState } from 'react'

import { t } from '@/i18n'
import type { ApprovalResult } from '@/protocol/protocol.generated'
import { answerApproval, type Card } from '@/store/app'

import { CardAnswering } from './card-answering'
import { CardFrame, Choice, Choices, FIELD, Target } from './card-frame'

type ApprovalCardProps = { card: Extract<Card, { kind: 'approval' }> }

/**
 * The approval, titled by what the call would do (Run this command?) over exactly what would run:
 * once, this conversation, always, or deny with a reason the model reads, so it can do
 * differently. When the guardian judged the call dangerous, the user may overrule it only for this
 * one call.
 */
export function ApprovalCard({ card }: ApprovalCardProps) {
  const { params } = card
  const [reason, setReason] = useState('')
  const title = t.approval.title(params.tool)

  if (card.answered) {
    return <CardAnswering testId="approval-card" title={title} />
  }

  const choose = (choice: ApprovalResult['choice']) => {
    answerApproval(card.id, choice === 'deny' && reason.trim() ? { choice, reason: reason.trim() } : { choice })
  }
  const wider = !params.guardian_denied
  const choices: (readonly [ApprovalResult['choice'], string])[] = [
    ['once', t.approval.once],
    ...(wider && params.allow_conversation ? [['conversation', t.approval.conversation] as const] : []),
    ...(wider && params.allow_always ? [['always', t.approval.always] as const] : []),
    ['deny', t.approval.deny]
  ]

  return (
    <CardFrame testId="approval-card" title={title}>
      <Target>{params.target}</Target>
      {params.reason && <p className="text-soft">{params.reason}</p>}
      {params.guardian_denied && <p className="font-medium text-failed-text">{t.approval.guardianDenied}</p>}
      <Choices>
        {choices.map(([choice, label], index) => (
          <Choice
            index={index}
            key={choice}
            label={label}
            onClick={() => {
              choose(choice)
            }}
            primary={index === 0}
          />
        ))}
      </Choices>
      <input
        aria-label={t.approval.reasonPlaceholder}
        className={FIELD}
        onChange={event => {
          setReason(event.target.value)
        }}
        placeholder={t.approval.reasonPlaceholder}
        value={reason}
      />
    </CardFrame>
  )
}
