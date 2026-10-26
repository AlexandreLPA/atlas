import type { ReactNode } from 'react'

import { Alert } from './icons'

/**
 * Why something could not be read or done, in its own place and in plain words, with the way out
 * when there is one (`action`): a list that failed says so instead of looking empty or old, and a
 * turn that failed offers what can fix it (the UI review, 25/10/2026). `live`, a screen reader says
 * it as it comes; an old failure in a conversation read again is only there to be read.
 */
export function InlineError({
  action,
  children,
  live = true,
  testId
}: {
  action?: ReactNode
  children: ReactNode
  live?: boolean
  testId?: string
}) {
  return (
    <div
      className="flex items-start gap-2 rounded-md border border-failed/30 bg-failed/10 px-3 py-2 text-sm text-failed-text"
      data-testid={testId}
      role={live ? 'alert' : undefined}
    >
      <Alert aria-hidden className="mt-0.5 shrink-0" size={16} stroke={1.75} />
      <div className="min-w-0 flex-1">{children}</div>
      {action}
    </div>
  )
}
