import { useStore } from '@nanostores/react'

import { t } from '@/i18n'
import type { BotRow } from '@/protocol/protocol.generated'
import { $bots, $read, $readError, $selected, $used, $view, newBot, openBot } from '@/store/app'
import { byLastUse, lastUse } from '@/store/status'

import { BotFace } from './bot-face'
import { ReadFailed } from './read-failed'
import { StatusDot, useConversationDot } from './status-dot'
import { Plus } from './ui/icons'
import { SkeletonRows } from './ui/skeleton'
import { when } from './when'

/**
 * The home's bots, kept short (decided on 13/10/2026): one row per bot with its
 * face, its title, when its conversation last changed and the last thing said in it. A click opens
 * the bot's fixed conversation, always the same one. Sections, pins and hiding are left out until
 * use asks. The last used comes first, and its face carries its conversation's dot (status-dot.tsx).
 */
export function BotsList() {
  const used = useStore($used)
  const bots = byLastUse(useStore($bots), bot => lastUse(bot.updated_at, used[bot.conversation_id ?? '']))
  const selected = useStore($selected)
  const view = useStore($view)
  const read = useStore($read).bots
  const failed = useStore($readError).bots

  return (
    <>
      <div className="px-3 pb-2">
        <button
          className="flex w-full items-center gap-2 rounded-md border border-neutral-300 px-2 py-1.5 text-left text-sm font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
          data-testid="new-bot"
          onClick={newBot}
          type="button"
        >
          <Plus aria-hidden size={16} stroke={1.75} />
          {t.bots.new}
        </button>
      </div>
      <ul className="flex-1 overflow-y-auto px-2 pb-3">
        {!read && bots.length === 0 && (
          <li className={failed ? 'px-1' : ''}>
            {failed ? <ReadFailed error={failed} what="bots" /> : <SkeletonRows label={t.bots.reading} />}
          </li>
        )}
        {read && bots.length === 0 && <li className="px-2 py-1 text-sm text-neutral-500">{t.bots.empty}</li>}
        {bots.map(bot => (
          <li key={bot.name}>
            <Row bot={bot} current={view === 'conversation' && selected !== null && selected === bot.conversation_id} />
          </li>
        ))}
      </ul>
    </>
  )
}

function Row({ bot, current }: { bot: BotRow; current: boolean }) {
  const dot = useConversationDot(bot.conversation_id)

  return (
    <button
      aria-current={current ? 'page' : undefined}
      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left ${
        current ? 'bg-neutral-200 dark:bg-neutral-800' : 'hover:bg-neutral-100 dark:hover:bg-neutral-800/60'
      }`}
      data-bot={bot.name}
      data-testid="bot-row"
      onClick={() => void openBot(bot.name)}
      title={bot.description || bot.title}
      type="button"
    >
      <span className="relative shrink-0">
        <BotFace name={bot.name} title={bot.title} />
        <StatusDot dot={dot} ring />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-medium">{bot.title}</span>
          <span className="shrink-0 text-xs text-neutral-500">{when(bot.updated_at)}</span>
        </span>
        <span className="block truncate text-xs text-neutral-500">
          <span className="font-mono">@{bot.name}</span>
          {(bot.preview || bot.description) && ` · ${bot.preview || bot.description}`}
        </span>
      </span>
    </button>
  )
}
