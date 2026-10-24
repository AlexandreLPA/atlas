import { type ReactNode, useState } from 'react'

import { t } from '@/i18n'
import type { ProposalApproved, ProposalChange, ProposalRow } from '@/protocol/protocol.generated'
import { approveProposal, discardProposal, putAwayApproved } from '@/store/app'

type ProposalCardProps = {
  /** What approving it did, once it was approved here. */
  approved?: ProposalApproved
  proposal: ProposalRow
  /** Where it came from, for the list of every proposal. */
  source?: ReactNode
}

/**
 * A memory proposal waiting for the user, drawn as a card (docs/ARCHITECTURE.md,
 * Desktop app). It shows the whole entry each change takes out and the whole new text, since a
 * replace overwrites the whole entry, with approve and discard. One that can no
 * longer apply says why and offers only discard (decided on 11/10/2026, so it is not found out
 * only on approval); one approved here says what it overwrote or removed.
 */
export function ProposalCard({ approved, proposal, source }: ProposalCardProps) {
  const [busy, setBusy] = useState(false)
  // The profile is the home's, whoever proposed; a bot's memory is its own.
  const title =
    proposal.target === 'user'
      ? t.proposals.titleUser
      : proposal.bot
        ? t.proposals.titleBotMemory(proposal.bot)
        : t.proposals.titleMemory

  const act = (action: () => Promise<void>) => {
    setBusy(true)
    action()
      .catch(() => undefined)
      .finally(() => {
        setBusy(false)
      })
  }

  return (
    <section
      aria-label={title}
      className="space-y-3 rounded-lg border border-violet-300 bg-violet-50 p-4 dark:border-violet-800 dark:bg-violet-950/40"
      data-proposal={proposal.id}
      data-testid="proposal-card"
    >
      <h2 className="text-sm font-semibold">{title}</h2>
      {source}
      <ul className="space-y-2 text-sm">
        {proposal.changes.map((change, index) => (
          // The same change can come twice in one call; its place is its identity.
          <ChangeView change={change} key={index} />
        ))}
      </ul>
      {approved ? (
        <Approved proposalId={proposal.id} result={approved} />
      ) : (
        <>
          {proposal.problem && (
            <p className="text-sm text-amber-800 dark:text-amber-300" data-testid="proposal-problem">
              {proposal.problem}
            </p>
          )}
          <div className="flex gap-2">
            {!proposal.problem && (
              <button
                className="rounded-md bg-violet-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                disabled={busy}
                onClick={() => {
                  act(() => approveProposal(proposal))
                }}
                type="button"
              >
                {t.proposals.approve}
              </button>
            )}
            <button
              className="rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900"
              disabled={busy}
              onClick={() => {
                act(() => discardProposal(proposal.id))
              }}
              type="button"
            >
              {t.proposals.discard}
            </button>
          </div>
        </>
      )}
    </section>
  )
}

function ChangeView({ change }: { change: ProposalChange }) {
  const gone = 'whitespace-pre-wrap text-neutral-600 line-through decoration-red-500/70 dark:text-neutral-400'
  const kept = 'whitespace-pre-wrap text-green-800 dark:text-green-300'

  switch (change.action) {
    case 'add':
      return (
        <li>
          <Label text={t.proposals.adds} />
          <p className={kept}>{change.content}</p>
        </li>
      )

    case 'remove':
      return (
        <li>
          <Label text={t.proposals.removes} />
          <p className={gone}>{change.entry}</p>
        </li>
      )

    case 'replace':
      return (
        <li>
          <Label text={t.proposals.replaces} />
          <p className={gone}>{change.entry}</p>
          <Label text={t.proposals.with} />
          <p className={kept}>{change.content}</p>
        </li>
      )
  }
}

function Label({ text }: { text: string }) {
  return <p className="text-xs font-medium tracking-wide text-neutral-500 uppercase">{text}</p>
}

function Approved({ proposalId, result }: { proposalId: string; result: ProposalApproved }) {
  const lost = result.replaced.length + result.removed.length > 0

  return (
    <div className="space-y-2 text-sm" data-testid="proposal-applied">
      <p className="font-medium">{t.proposals.applied}</p>
      {result.replaced.length > 0 && (
        <div>
          <p className="text-neutral-500">{t.proposals.overwrote}</p>
          {result.replaced.map(entry => (
            <p className="whitespace-pre-wrap" key={entry}>
              {entry}
            </p>
          ))}
        </div>
      )}
      {result.removed.length > 0 && (
        <div>
          <p className="text-neutral-500">{t.proposals.removed}</p>
          {result.removed.map(entry => (
            <p className="whitespace-pre-wrap" key={entry}>
              {entry}
            </p>
          ))}
        </div>
      )}
      {lost && <p className="text-neutral-500">{t.proposals.readd}</p>}
      <button
        className="rounded-md border border-neutral-300 bg-white px-3 py-1.5 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900"
        onClick={() => {
          putAwayApproved(proposalId)
        }}
        type="button"
      >
        {t.proposals.ok}
      </button>
    </div>
  )
}
