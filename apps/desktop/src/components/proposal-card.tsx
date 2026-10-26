import { type ReactNode, useState } from 'react'

import { t } from '@/i18n'
import type { ProposalApproved, ProposalChange, ProposalRow } from '@/protocol/protocol.generated'
import { approveProposal, discardProposal, putAwayApproved } from '@/store/app'

import { Brain } from './ui/icons'
import { shared, wordDiff } from './word-diff'

type ProposalCardProps = {
  /** What approving it did, once it was approved here. */
  approved?: ProposalApproved
  proposal: ProposalRow
  /** Where it came from, for the list of every proposal. */
  source?: ReactNode
}

/**
 * A memory proposal waiting for the user, drawn as a card as neutral as the app's other panels
 * (docs/ARCHITECTURE.md, Desktop app). A replace shows what differs, word by word, with the whole
 * entry and the whole new text a click away, since it overwrites the whole entry; then approve and
 * discard. One that can no longer apply says why and offers only discard (decided on 11/10/2026, so it is not found out
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
      className="enter space-y-3 rounded-lg border border-border bg-raised p-4"
      data-proposal={proposal.id}
      data-testid="proposal-card"
    >
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Brain aria-hidden className="text-muted-foreground" size={16} stroke={1.75} />
        {title}
      </h2>
      {source}
      <ul className="space-y-3 text-sm">
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
            <p className="text-sm text-waiting-text" data-testid="proposal-problem">
              {proposal.problem}
            </p>
          )}
          <div className="flex gap-2">
            {!proposal.problem && (
              <button
                className={PRIMARY}
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
              className={SECONDARY}
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
  switch (change.action) {
    case 'add':
      return (
        <li className="space-y-1">
          <Label text={t.proposals.adds} />
          <p className={`${BOX} ${ADDED}`}>{change.content}</p>
        </li>
      )

    case 'remove':
      return (
        <li className="space-y-1">
          <Label text={t.proposals.removes} />
          <p className={`${BOX} ${REMOVED}`}>{change.entry}</p>
        </li>
      )

    case 'replace':
      return (
        <li className="space-y-1">
          <Label text={t.proposals.replaces} />
          <Replaced after={change.content} before={change.entry ?? ''} />
        </li>
      )
  }
}

/**
 * A replace overwrites the whole entry, but what the user has to judge is what differs: the entry
 * once, with the words that go struck and the words that come marked (asked for by the user on
 * 24/10/2026). When the two share too little for that to read, the old text above the new. Both
 * whole stay a click away.
 */
function Replaced({ after, before }: { after: string; before: string }) {
  const pieces = wordDiff(before, after)

  return (
    <>
      {shared(pieces) < 0.3 ? (
        <div className="space-y-1">
          <p className={`${BOX} ${REMOVED}`}>{before}</p>
          <p className={`${BOX} ${ADDED}`}>{after}</p>
        </div>
      ) : (
        <p className={BOX} data-testid="proposal-diff">
          {pieces.map((piece, index) =>
            piece.kind === 'same' ? (
              piece.text
            ) : piece.kind === 'removed' ? (
              // Pieces come in order and repeat; their place is their identity.
              <del className={`rounded-sm px-0.5 ${REMOVED}`} key={index}>
                {piece.text}
              </del>
            ) : (
              <ins className={`rounded-sm px-0.5 no-underline ${ADDED}`} key={index}>
                {piece.text}
              </ins>
            )
          )}
        </p>
      )}
      <details className="group text-xs text-muted-foreground">
        <summary className="cursor-pointer select-none hover:text-foreground">{t.proposals.whole}</summary>
        <div className="mt-2 space-y-2">
          <Label text={t.proposals.before} />
          <p className={BOX}>{before}</p>
          <Label text={t.proposals.after} />
          <p className={BOX}>{after}</p>
        </div>
      </details>
    </>
  )
}

function Label({ text }: { text: string }) {
  return <p className="text-xs font-medium text-muted-foreground">{text}</p>
}

function Approved({ proposalId, result }: { proposalId: string; result: ProposalApproved }) {
  const lost = result.replaced.length + result.removed.length > 0

  return (
    <div className="space-y-2 text-sm" data-testid="proposal-applied">
      <p className="font-medium">{t.proposals.applied}</p>
      {result.replaced.length > 0 && (
        <div>
          <p className="text-muted-foreground">{t.proposals.overwrote}</p>
          {result.replaced.map(entry => (
            <p className="whitespace-pre-wrap" key={entry}>
              {entry}
            </p>
          ))}
        </div>
      )}
      {result.removed.length > 0 && (
        <div>
          <p className="text-muted-foreground">{t.proposals.removed}</p>
          {result.removed.map(entry => (
            <p className="whitespace-pre-wrap" key={entry}>
              {entry}
            </p>
          ))}
        </div>
      )}
      {lost && <p className="text-muted-foreground">{t.proposals.readd}</p>}
      <button
        className={SECONDARY}
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

const PRIMARY = 'rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-40'
const SECONDARY =
  'rounded-md border border-border-strong px-3 py-1.5 text-sm text-soft hover:bg-muted disabled:opacity-40'
/** An entry's text, as the memory's screen draws one. */
const BOX = 'rounded-md border border-border px-3 py-2 text-sm whitespace-pre-wrap text-foreground'
const REMOVED = 'bg-removed/15 line-through decoration-removed decoration-1'
const ADDED = 'bg-added/15'
