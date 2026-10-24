import { useState } from 'react'

import { t } from '@/i18n'
import type { ApprovalResult } from '@/protocol/protocol.generated'
import { answerApproval, type Card } from '@/store/app'

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

  const choose = (choice: ApprovalResult['choice']) => {
    answerApproval(card.id, choice === 'deny' && reason.trim() ? { choice, reason: reason.trim() } : { choice })
  }

  return (
    <section
      aria-label={t.approval.title(params.tool)}
      className="space-y-3 rounded-lg border border-orange-300 bg-orange-50 p-4 dark:border-orange-800 dark:bg-orange-950/40"
      data-testid="approval-card"
    >
      <h2 className="text-sm font-semibold">{t.approval.title(params.tool)}</h2>
      <pre className="max-h-48 overflow-auto rounded bg-white p-2 font-mono text-xs whitespace-pre-wrap dark:bg-neutral-900">
        {params.target}
      </pre>
      {params.reason && <p className="text-sm text-neutral-600 dark:text-neutral-300">{params.reason}</p>}
      {params.guardian_denied && (
        <p className="text-sm font-medium text-red-700 dark:text-red-400">{t.approval.guardianDenied}</p>
      )}
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
        className="w-full rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
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
        primary
          ? 'bg-orange-600 text-white'
          : 'border border-neutral-300 bg-white hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900'
      }`}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  )
}
