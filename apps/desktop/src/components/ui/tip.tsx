import type { Icon } from '@tabler/icons-react'
import { Tooltip } from 'radix-ui'
import type { ReactElement, ReactNode } from 'react'

/**
 * What a control does, shown while the pointer rests on it or the keyboard is on it: Radix's
 * tooltip. Each brings its own provider, so a control shows it wherever it is drawn.
 */
export function Tip({
  children,
  label,
  side = 'top'
}: {
  children: ReactElement
  label: ReactNode
  side?: 'bottom' | 'left' | 'right' | 'top'
}) {
  return (
    <Tooltip.Provider delayDuration={400}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className="z-50 max-w-64 rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground shadow-md"
            side={side}
            sideOffset={6}
          >
            {label}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  )
}

/** A control that is only its icon (asked for by the user on 18/10/2026, where an icon says it
 * plainly): its name is the tooltip and what a screen reader reads. */
export function IconButton({
  className = '',
  disabled,
  icon: Glyph,
  label,
  onClick,
  pressed,
  side,
  testId
}: {
  className?: string
  disabled?: boolean
  icon: Icon
  label: string
  onClick: () => void
  pressed?: boolean
  side?: 'bottom' | 'left' | 'right' | 'top'
  testId?: string
}) {
  return (
    <Tip label={label} side={side}>
      <button
        aria-label={label}
        aria-pressed={pressed}
        className={`${ICON_BUTTON} ${className}`}
        data-testid={testId}
        disabled={disabled}
        onClick={onClick}
        type="button"
      >
        <Glyph aria-hidden size={16} stroke={1.75} />
      </button>
    </Tip>
  )
}

export const ICON_BUTTON =
  'inline-flex shrink-0 items-center justify-center rounded-md p-1 text-muted-foreground hover:bg-selected hover:text-foreground disabled:opacity-40 aria-pressed:bg-selected aria-pressed:text-foreground'
