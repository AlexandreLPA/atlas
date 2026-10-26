import type { ReactNode } from 'react'

import { t } from '@/i18n'
import { showConversation } from '@/store/app'

/**
 * The head of a page of its own (the routines, the memory proposals, the marketplace), the same on
 * each (the UI review, 26/10/2026): its title and what it is for, its own controls on the right, and
 * the way back to the conversation always in the same place.
 */
export function PageHeader({
  actions,
  children,
  intro,
  title
}: {
  /** Its own controls, before the way back. */
  actions?: ReactNode
  /** What goes under its intro, such as links. */
  children?: ReactNode
  intro: string
  title: string
}) {
  return (
    <header className="space-y-1">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">{title}</h1>
        <div className="flex shrink-0 items-center gap-2">
          {actions}
          <button
            className="rounded-md px-3 py-1.5 text-sm text-soft hover:bg-muted"
            data-testid="page-back"
            onClick={showConversation}
            type="button"
          >
            {t.proposals.back}
          </button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">{intro}</p>
      {children}
    </header>
  )
}
