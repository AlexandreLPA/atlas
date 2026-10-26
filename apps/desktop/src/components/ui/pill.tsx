import type { SelectHTMLAttributes } from 'react'

import { ChevronDown } from './icons'

/**
 * The controls above the box, all one pill (asked for by the user on 18/10/2026, the terminal's
 * button the model): the model, the effort and the terminal side by side.
 */
export const PILL =
  'inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-border-strong px-2 text-xs text-soft hover:bg-muted disabled:opacity-40'

/** A pill that is on (the terminal's panel open). */
export const PILL_ON = 'border-faint bg-selected text-foreground'

/** A choice as a pill: the native list, drawn as the other pills with a chevron of its own. */
export function PillSelect({ children, className = '', ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className="relative inline-flex">
      <select {...props} className={`${PILL} max-w-56 appearance-none truncate bg-transparent pr-6 ${className}`}>
        {children}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-1.5 -translate-y-1/2 text-muted-foreground"
        size={12}
        stroke={2}
      />
    </span>
  )
}
