import { useStore } from '@nanostores/react'
import type { Icon } from '@tabler/icons-react'
import type { ReactNode } from 'react'

import { t } from '@/i18n'
import { $contributions, $pluginPage, inArea } from '@/plugins/registry'
import { host } from '@/plugins/sdk'
import {
  $bots,
  $cards,
  $conversations,
  $pairings,
  $proposals,
  $read,
  $readError,
  $selected,
  $sidebarTab,
  $used,
  $view,
  $workbench,
  newConversation,
  selectConversation,
  showMarketplace,
  showProposals,
  showRoutines,
  showSidebarTab,
  type SidebarTab
} from '@/store/app'
import { $sidebarOpen, toggleSidebar } from '@/store/layout'
import { openSettings } from '@/store/settings'
import { byLastUse, type Dot, lastUse, worst } from '@/store/status'

import { BotsList } from './bots-list'
import { ExecutorsList } from './executors-list'
import { HomeBar } from './home-bar'
import { RailEdge } from './rail-edge'
import { ReadFailed } from './read-failed'
import { StatusDot, useConversationDot, useConversationsDot, useInstancesDot } from './status-dot'
import { Bulb, CalendarTime, LayoutSidebar, MessagePlus, Messages, Plug, Robot, Store, Terminal } from './ui/icons'
import { SkeletonRows } from './ui/skeleton'
import { IconButton, Tip } from './ui/tip'
import { UpdateBar, UpdateStrip } from './update-bar'
import { when } from './when'

/**
 * The conversations, the latest activity first;
 * above them, while any wait, how many memory proposals there are (step 9b); and at the foot,
 * always, the home on screen with the others and what the app does with homes (step 10c),
 * with the app's update above it while there is one. The list is the conversations,
 * the bots or the executors (step 16f), whose tab says
 * when one of their sessions waits for the user. Hidden (asked for by
 * the user on 16/10/2026), it leaves a strip that brings it back and still says when something
 * waits, or that an update is there. Each entry has its icon, and the tabs are only theirs, named
 * on hover (asked for by the user on 18/10/2026): their words did not fit. Its edge toward the
 * conversation drags it wider or narrower (store/layout.ts).
 */
export function Sidebar() {
  const proposals = useStore($proposals)
  const view = useStore($view)
  // The plugins' pages (step 16m), each an entry below the routines.
  const pages = inArea(useStore($contributions), 'page')
  const pluginPage = useStore($pluginPage)
  const tab = useStore($sidebarTab)
  const bots = useStore($bots)
  const waiting = useStore($pairings).filter(pairing => !pairing.approved).length
  const cards = useStore($cards)
  const { instances } = useStore($workbench)
  const asking =
    cards.filter(card => card.kind === 'executor-approval' || card.kind === 'executor-question').length ||
    instances.filter(row => row.waiting > 0).length
  const open = useStore($sidebarOpen)
  const conversationsDot = useConversationsDot(useStore($conversations).map(row => row.id))
  const botsDot = useConversationsDot(bots.map(bot => bot.conversation_id))
  const executorsDot = useInstancesDot(() => true)

  if (!open) {
    return (
      <nav
        aria-label="Conversations"
        className="flex w-9 shrink-0 flex-col items-center gap-2 border-r border-neutral-200 bg-neutral-50 py-3 dark:border-neutral-800 dark:bg-neutral-900"
      >
        <IconButton
          icon={LayoutSidebar}
          label={t.layout.showSidebar}
          onClick={() => {
            toggleSidebar(true)
          }}
          side="right"
          testId="sidebar-show"
        />
        {asking > 0 ? (
          <span className="rounded-full bg-amber-500 px-1.5 text-xs leading-4 font-medium text-white">{asking}</span>
        ) : (
          <StatusDot dot={worst([conversationsDot, botsDot, executorsDot])} />
        )}
        <UpdateStrip />
      </nav>
    )
  }

  return (
    <nav
      aria-label="Conversations"
      className="sidebar-open flex flex-col border-r border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900"
    >
      <div className="space-y-1 p-3">
        <div className="flex items-center gap-1 pb-1">
          <IconButton
            icon={LayoutSidebar}
            label={t.layout.hideSidebar}
            onClick={() => {
              toggleSidebar(false)
            }}
            side="bottom"
            testId="sidebar-hide"
          />
          <div className="flex min-w-0 flex-1 rounded-md bg-neutral-200 p-0.5 dark:bg-neutral-800" role="tablist">
            <Tab current={tab} dot={conversationsDot} icon={Messages} label={t.conversations.tab} tab="conversations" />
            <Tab count={bots.length} current={tab} dot={botsDot} icon={Robot} label={t.bots.tab} tab="bots" />
            <Tab
              alert={asking}
              current={tab}
              dot={executorsDot}
              icon={Terminal}
              label={t.executors.tab}
              tab="executors"
            />
          </div>
        </div>
        <Entry
          current={view === 'routines'}
          icon={CalendarTime}
          label={t.routines.title}
          onClick={showRoutines}
          testId="routines-entry"
        />
        <Entry
          current={view === 'marketplace'}
          icon={Store}
          label={t.marketplace.title}
          onClick={showMarketplace}
          testId="marketplace-entry"
        />
        {pages.map(page => (
          <Entry
            current={view === 'plugin' && pluginPage === page.key}
            icon={Plug}
            key={page.key}
            label={page.title}
            onClick={() => {
              host.openPage(page.key)
            }}
            testId={`plugin-page-${page.key}`}
          />
        ))}
        {waiting > 0 && (
          // The soul and the plugins live on the settings screen (step 15b); someone waiting with a
          // code still shows here, as the proposals do, and opens them there.
          <Entry
            current={false}
            icon={Plug}
            label={t.plugins.title}
            onClick={() => {
              openSettings('plugins')
            }}
            testId="plugins-entry"
          >
            <span
              className="rounded-full bg-amber-500 px-2 text-xs leading-5 font-medium text-white"
              data-testid="plugins-waiting"
            >
              {t.plugins.waiting(waiting)}
            </span>
          </Entry>
        )}
        {proposals.length > 0 && (
          <Entry
            current={view === 'proposals'}
            icon={Bulb}
            label={t.proposals.title}
            onClick={showProposals}
            testId="proposals-entry"
            tone="violet"
          >
            <span className="rounded-full bg-violet-600 px-2 text-xs leading-5 font-medium text-white">
              {proposals.length}
            </span>
          </Entry>
        )}
      </div>
      {/* The tab's name heads its list, the tabs being only icons (asked for by the user on
          22/10/2026). */}
      <h2 className="px-5 pb-2 text-sm font-semibold" data-testid="sidebar-title">
        {tab === 'bots' ? t.bots.tab : tab === 'executors' ? t.executors.tab : t.conversations.tab}
      </h2>
      {tab === 'bots' ? <BotsList /> : tab === 'executors' ? <ExecutorsList /> : <Conversations />}
      <UpdateBar />
      <HomeBar />
      <RailEdge rail="sidebar" />
    </nav>
  )
}

/** One of the sidebar's screens: its icon and its name, and what waits there. */
function Entry({
  children,
  current,
  icon: Glyph,
  label,
  onClick,
  testId,
  tone = 'neutral'
}: {
  children?: ReactNode
  current: boolean
  icon: Icon
  label: string
  onClick: () => void
  testId: string
  tone?: 'neutral' | 'violet'
}) {
  const on =
    tone === 'violet'
      ? 'bg-violet-100 font-medium dark:bg-violet-950'
      : 'bg-neutral-200 font-medium dark:bg-neutral-800'

  return (
    <button
      aria-current={current ? 'page' : undefined}
      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
        current ? on : 'hover:bg-neutral-100 dark:hover:bg-neutral-800'
      }`}
      data-testid={testId}
      onClick={onClick}
      type="button"
    >
      <Glyph aria-hidden className="shrink-0 text-neutral-500" size={16} stroke={1.75} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {children}
    </button>
  )
}

function Tab({
  alert,
  count,
  current,
  dot = '',
  icon: Glyph,
  label,
  tab
}: {
  /** How many things wait for the user there: a badge, not a count of rows. */
  alert?: number
  count?: number
  current: SidebarTab
  /** The most urgent dot of its rows, shown while no badge says more. */
  dot?: Dot
  icon: Icon
  label: string
  tab: SidebarTab
}) {
  return (
    <Tip label={count ? `${label} (${String(count)})` : label} side="bottom">
      <button
        aria-label={label}
        aria-selected={current === tab}
        className={`relative flex min-w-0 flex-1 items-center justify-center gap-1 rounded px-2 py-1 ${
          current === tab
            ? 'bg-white text-neutral-900 shadow-sm dark:bg-neutral-950 dark:text-neutral-100'
            : 'text-neutral-500 hover:text-neutral-800 dark:text-neutral-400 dark:hover:text-neutral-200'
        }`}
        data-testid={`tab-${tab}`}
        onClick={() => {
          showSidebarTab(tab)
        }}
        role="tab"
        type="button"
      >
        <Glyph aria-hidden size={16} stroke={1.75} />
        {count ? <span className="text-xs text-neutral-500">{count}</span> : null}
        {alert ? (
          <span
            className="rounded-full bg-amber-500 px-1.5 text-xs leading-4 font-medium text-white"
            data-testid={`tab-${tab}-alert`}
          >
            {alert}
          </span>
        ) : (
          <StatusDot dot={dot} />
        )}
      </button>
    </Tip>
  )
}

/** The conversations, the last used first: one whose turn began here goes up at once, before its
 * row is read again. */
function Conversations() {
  const used = useStore($used)
  const conversations = byLastUse(useStore($conversations), row => lastUse(row.updated_at, used[row.id]))
  const selected = useStore($selected)
  const view = useStore($view)
  const read = useStore($read).conversations
  const failed = useStore($readError).conversations

  return (
    <>
      <div className="px-3 pb-2">
        <button
          className="flex w-full items-center gap-2 rounded-md border border-neutral-300 px-2 py-1.5 text-left text-sm font-medium hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
          onClick={newConversation}
          type="button"
        >
          <MessagePlus aria-hidden size={16} stroke={1.75} />
          {t.conversations.new}
        </button>
      </div>
      <ul className="flex-1 overflow-y-auto px-2 pb-3">
        {!read && conversations.length === 0 && (
          <li className={failed ? 'px-1' : ''}>
            {failed ? (
              <ReadFailed error={failed} what="conversations" />
            ) : (
              <SkeletonRows label={t.conversations.reading} />
            )}
          </li>
        )}
        {read && conversations.length === 0 && (
          <li className="px-2 py-1 text-sm text-neutral-500">{t.conversations.empty}</li>
        )}
        {conversations.map(conversation => (
          <li key={conversation.id}>
            <button
              aria-current={conversation.id === selected && view === 'conversation' ? 'page' : undefined}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                conversation.id === selected && view === 'conversation'
                  ? 'bg-neutral-200 font-medium dark:bg-neutral-800'
                  : 'hover:bg-neutral-100 dark:hover:bg-neutral-800/60'
              }`}
              data-testid="conversation-row"
              onClick={() => void selectConversation(conversation.id)}
              title={conversation.title}
              type="button"
            >
              <ConversationDot conversationId={conversation.id} />
              <span className="min-w-0 flex-1 truncate" data-testid="conversation-title">
                {conversation.title || t.conversations.untitled}
                {conversation.source !== 'app' && (
                  <span className="ml-1 text-xs font-normal text-neutral-500" data-testid="conversation-via">
                    {t.conversations.via(conversation.source)}
                  </span>
                )}
              </span>
              <span className="shrink-0 text-xs font-normal text-neutral-500" data-testid="conversation-when">
                {when(conversation.updated_at)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  )
}

function ConversationDot({ conversationId }: { conversationId: string }) {
  return <StatusDot dot={useConversationDot(conversationId)} slot />
}
