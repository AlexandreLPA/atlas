import { useState } from 'react'

import { t } from '@/i18n'
import type { ApprovalResult } from '@/protocol/protocol.generated'
import { answerApproval, type Card } from '@/store/app'

import { CardAnswering } from './card-answering'

type ApprovalCardProps = { card: Extract<Card, { kind: 'approval' }> }

/**
 * The approval: once, this conversation, always, or deny with a reason the model reads, so it can
 * do differently. When the guardian judged the call
 * dangerous, the user may overrule it only for this one call.
 */
export function ApprovalCard({ card }: ApprovalCardProps) {
  const { params } = card
  const [reason, setReason] = useState('')
  const wider = !params.guardian_denied

  if (card.answered) {
    return <CardAnswering testId="approval-card" title={t.approval.title(params.tool)} />
  }

  const choose = (choice: ApprovalResult['choice']) => {
    answerApproval(card.id, choice === 'deny' && reason.trim() ? { choice, reason: reason.trim() } : { choice })
  }

  return (
    <section
      aria-label={t.approval.title(params.tool)}
      className="enter space-y-3 rounded-lg border border-waiting/40 bg-waiting/10 p-4"
      data-testid="approval-card"
    >
      <h2 className="text-sm font-semibold">{t.approval.title(params.tool)}</h2>
      <pre className="max-h-48 overflow-auto rounded bg-raised p-2 font-mono text-xs whitespace-pre-wrap">
        {params.target}
      </pre>
      {params.reason && <p className="text-sm text-soft">{params.reason}</p>}
      {params.guardian_denied && <p className="text-sm font-medium text-failed-text">{t.approval.guardianDenied}</p>}
      <div className="flex flex-wrap gap-2">
        <Choice
          label={t.approval.once}
          onClick={() => {
            choose('once')
          }}
          primary
        />
        {wider && params.allow_conversation && (
          <Choice
            label={t.approval.conversation}
            onClick={() => {
              choose('conversation')
            }}
          />
        )}
        {wider && params.allow_always && (
          <Choice
            label={t.approval.always}
            onClick={() => {
              choose('always')
            }}
          />
        )}
        <Choice
          label={t.approval.deny}
          onClick={() => {
            choose('deny')
          }}
        />
      </div>
      <input
        aria-label={t.approval.reasonPlaceholder}
        className="w-full rounded-md border border-border-strong bg-raised px-3 py-1.5 text-sm"
        onChange={event => {
          setReason(event.target.value)
        }}
        placeholder={t.approval.reasonPlaceholder}
        value={reason}
      />
    </section>
  )
}

function Choice({ label, onClick, primary = false }: { label: string; onClick: () => void; primary?: boolean }) {
  return (
    <button
      className={`rounded-md px-3 py-1.5 text-sm font-medium ${
        primary ? 'bg-primary text-primary-foreground' : 'border border-border-strong bg-raised hover:bg-muted'
      }`}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  )
}
