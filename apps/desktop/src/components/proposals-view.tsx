import { useStore } from '@nanostores/react'

import { t } from '@/i18n'
import type { ProposalRow } from '@/protocol/protocol.generated'
import {
  $approved,
  $conversations,
  $proposals,
  $readError,
  type Approved,
  selectConversation,
  showConversation
} from '@/store/app'

import { ProposalCard } from './proposal-card'
import { ReadFailed } from './read-failed'

/**
 * Every memory proposal waiting for the user, as a screen: global,
 * since the memory is one, and so a proposal is never lost from sight in a conversation nobody
 * opens again.
 */
export function ProposalsView() {
  const proposals = useStore($proposals)
  const approved = useStore($approved)
  const done = approvedHere(approved, proposals, () => true)
  const failed = useStore($readError).proposals

  return (
    <div className="flex-1 overflow-y-auto" data-testid="proposals-view">
      <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
        <div className="flex items-center justify-between gap-4">
          <h1 className="text-lg font-semibold">{t.proposals.title}</h1>
          <button
            className="rounded-md px-3 py-1.5 text-sm text-soft hover:bg-muted"
            onClick={showConversation}
            type="button"
          >
            {t.proposals.back}
          </button>
        </div>
        <p className="text-sm text-muted-foreground">{t.proposals.intro}</p>
        {failed && <ReadFailed error={failed} what="proposals" />}
        {!failed && proposals.length === 0 && done.length === 0 && (
          <p className="text-muted-foreground">{t.proposals.empty}</p>
        )}
        {done.map(({ proposal, result }) => (
          <ProposalCard approved={result} key={proposal.id} proposal={proposal} />
        ))}
        {proposals.map(proposal => (
          <ProposalCard key={proposal.id} proposal={proposal} source={<Source proposal={proposal} />} />
        ))}
      </div>
    </div>
  )
}

/** The proposals approved here whose cards are still up, among those `wanted`. */
export function approvedHere(
  approved: Record<string, Approved | undefined>,
  open: ProposalRow[],
  wanted: (proposal: ProposalRow) => boolean
): Approved[] {
  return Object.values(approved).filter(
    (item): item is Approved =>
      item !== undefined && wanted(item.proposal) && !open.some(row => row.id === item.proposal.id)
  )
}

function Source({ proposal }: { proposal: ProposalRow }) {
  const conversations = useStore($conversations)
  const conversationId = proposal.conversation_id

  if (!conversationId) {
    return null
  }

  const title = conversations.find(row => row.id === conversationId)?.title || t.conversations.untitled

  return (
    <p className="text-xs text-muted-foreground">
      {t.proposals.fromConversation}{' '}
      <button
        className="underline hover:text-foreground"
        onClick={() => void selectConversation(conversationId)}
        type="button"
      >
        {title}
      </button>
    </p>
  )
}
