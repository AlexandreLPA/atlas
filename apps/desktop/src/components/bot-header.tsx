import { useStore } from '@nanostores/react'

import { t } from '@/i18n'
import type { BotRow } from '@/protocol/protocol.generated'
import { $routines, editBot, showRoutines } from '@/store/app'

import { BotFace } from './bot-face'

/**
 * Whose conversation this is, above a bot's fixed conversation: its face, its title and what it is
 * for, its routines (one click away, in the routines' screen, each marked with its bot), and the
 * way into its definition.
 */
export function BotHeader({ bot }: { bot: BotRow }) {
  const routines = useStore($routines).filter(routine => routine.bot === bot.name).length

  return (
    <header
      className="flex items-center gap-3 border-b border-neutral-200 px-6 py-3 dark:border-neutral-800"
      data-testid="bot-header"
    >
      <BotFace name={bot.name} size={36} title={bot.title} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">
          {bot.title} <span className="font-mono text-xs font-normal text-neutral-500">@{bot.name}</span>
        </p>
        {bot.description && <p className="truncate text-sm text-neutral-500">{bot.description}</p>}
      </div>
      {routines > 0 && (
        <button className={BUTTON} data-testid="bot-routines" onClick={showRoutines} type="button">
          {t.bots.routines(routines)}
        </button>
      )}
      <button className={BUTTON} data-testid="bot-edit" onClick={() => void editBot(bot.name)} type="button">
        {t.bots.edit}
      </button>
    </header>
  )
}

/** What a bot's conversation shows before anything was said: whose it is. */
export function BotEmpty({ bot }: { bot: BotRow }) {
  return (
    <div className="flex flex-col items-center gap-3 pt-20 text-center" data-testid="bot-empty">
      <BotFace name={bot.name} size={96} title={bot.title} />
      <p className="text-lg font-medium">{bot.title}</p>
      <p className="max-w-md text-sm text-neutral-500">{bot.description || t.bots.emptyChat(bot.title)}</p>
    </div>
  )
}

const BUTTON =
  'shrink-0 rounded-md border border-neutral-300 px-2 py-1 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800'
