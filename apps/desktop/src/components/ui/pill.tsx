import type { SelectHTMLAttributes } from 'react'

import { ChevronDown } from './icons'

/**
 * The controls above the box, all one pill (asked for by the user on 18/10/2026, the terminal's
 * button the model): the model, the effort and the terminal side by side.
 */
export const PILL =
  'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-neutral-300 px-2 text-xs text-neutral-600 hover:bg-neutral-100 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-neutral-400 disabled:opacity-40 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800'

/** A pill that is on (the terminal's panel open). */
export const PILL_ON =
  'border-neutral-400 bg-neutral-200 text-neutral-900 dark:border-neutral-600 dark:bg-neutral-800 dark:text-neutral-100'

/** A choice as a pill: the native list, drawn as the other pills with a chevron of its own. */
export function PillSelect({ children, className = '', ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className="relative inline-flex">
      <select {...props} className={`${PILL} max-w-56 appearance-none truncate bg-transparent pr-6 ${className}`}>
        {children}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-1.5 -translate-y-1/2 text-neutral-400"
        size={12}
        stroke={2}
      />
    </span>
  )
}
