import { useState } from 'react'

import { t } from '@/i18n'

/**
 * "Auto" of an executor's session, its harness's own automatic mode (Claude Code's `auto`): on by
 * default since 20/10/2026, at the user's word; turned back on only after the user read what it
 * does and said yes (decided with the user on 17/10/2026). Off goes at once.
 */
export function useHarnessAuto(set: (on: boolean) => void) {
  const [asking, setAsking] = useState(false)

  return {
    asking,
    cancel: () => {
      setAsking(false)
    },
    confirm: () => {
      setAsking(false)
      set(true)
    },
    toggle: (on: boolean) => {
      setAsking(on)

      if (!on) {
        set(false)
      }
    }
  }
}

/** What the user reads before the harness approves its own permission requests: for this session
 * only, under its own rules and the user's settings, to which Atlas adds none (decided with the user
 * on 17/10/2026). */
export function HarnessAutoWarning({
  onCancel,
  onConfirm,
  title
}: {
  onCancel: () => void
  onConfirm: () => void
  title: string
}) {
  return (
    <div
      className="space-y-2 rounded-lg border border-yellow-500 bg-yellow-50 p-3 text-sm dark:border-yellow-700 dark:bg-yellow-950/40"
      data-testid="harness-auto-warning"
      role="alertdialog"
    >
      <p className="font-medium">{t.executors.autoWarning(title)}</p>
      <p>{t.executors.autoWarningRules(title)}</p>
      <div className="flex flex-wrap gap-2">
        <button
          className="rounded bg-yellow-500 px-3 py-1 font-medium text-neutral-900"
          data-testid="harness-auto-confirm"
          onClick={onConfirm}
          type="button"
        >
          {t.executors.autoWarningConfirm}
        </button>
        <button
          className="rounded border border-neutral-300 bg-white px-3 py-1 dark:border-neutral-700 dark:bg-neutral-900"
          data-testid="harness-auto-cancel"
          onClick={onCancel}
          type="button"
        >
          {t.executors.autoWarningCancel}
        </button>
      </div>
    </div>
  )
}
