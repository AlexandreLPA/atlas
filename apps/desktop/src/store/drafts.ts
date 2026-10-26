/**
 * What the user typed in each box and has not sent, by whose box it is (a conversation's id, a
 * bot's conversation, an executor's session, `DRAFT` for a new conversation): switching to another
 * conversation leaves the words there, and coming back finds them (found by the UI review on
 * 25/10/2026: the words typed for one conversation stayed in the box and went to the next one with
 * an Enter). Kept by the window while it is open, never written anywhere.
 */
import { map } from 'nanostores'

export const $drafts = map<Record<string, string | undefined>>({})

/** The words waiting in `owner`'s box. */
export function draftOf(owner: string): string {
  return $drafts.get()[owner] ?? ''
}

/** Keep `text` as `owner`'s words; empty forgets them. */
export function setDraft(owner: string, text: string): void {
  $drafts.setKey(owner, text || undefined)
}
