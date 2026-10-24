import { useStore } from '@nanostores/react'
import { type CSSProperties, useEffect } from 'react'

import { t } from '@/i18n'
import { PluginPage } from '@/plugins/view'
import {
  $approved,
  $bots,
  $cards,
  $connection,
  $keyLater,
  $keyNotice,
  $modelOptions,
  $proposals,
  $selected,
  $session,
  $threads,
  $view,
  currentFolder,
  keyScreenShown
} from '@/store/app'
import { $panelWidth, $sidebarWidth, toggleSidebar } from '@/store/layout'
import { $terminals, openTerminal, refreshTerminalAvailable, terminalAvailable, togglePanel } from '@/store/terminals'
import { EMPTY_THREAD } from '@/store/thread'

import { ApprovalCard } from './approval-card'
import { AskCard } from './ask-card'
import { BotDialogHost } from './bot-dialog'
import { BotEmpty, BotHeader } from './bot-header'
import { Composer } from './composer'
import { HomeDialogHost } from './home-dialog'
import { KeyBand, KeyScreen } from './key-screen'
import { PreviewLinks } from './preview-links'
import { ProposalCard } from './proposal-card'
import { approvedHere, ProposalsView } from './proposals-view'
import { RoutinesView } from './routines-view'
import { SecretCard } from './secret-card'
import { SessionView } from './session-view'
import { SettingsView } from './settings-view'
import { SidePanel } from './side-panel'
import { Sidebar } from './sidebar'
import { ThreadView } from './thread-view'

export function App() {
  const selected = useStore($selected)
  const threads = useStore($threads)
  const cards = useStore($cards)
  const proposals = useStore($proposals)
  const approved = useStore($approved)
  const view = useStore($view)
  const thread = (selected ? threads[selected] : undefined) ?? EMPTY_THREAD
  // An executor's cards are in its session's conversation, in the executors tab (step 16f).
  const shown = cards.filter(
    (card): card is Extract<typeof card, { kind: 'approval' | 'ask' | 'secret' }> =>
      card.conversationId === selected && (card.kind === 'ask' || card.kind === 'approval' || card.kind === 'secret')
  )
  // A proposal shows in the conversation its review read, until it is decided.
  const fromHere = (proposal: { conversation_id: null | string }) =>
    selected !== null && proposal.conversation_id === selected
  const proposed = proposals.filter(fromHere)
  const done = approvedHere(approved, proposals, fromHere)
  const { state } = useStore($connection)
  const session = useStore($session)
  // A bot's fixed conversation says whose it is.
  const bot = useStore($bots).find(row => selected !== null && row.conversation_id === selected)
  const options = useStore($modelOptions)
  const later = useStore($keyLater)
  const notice = useStore($keyNotice)
  // With no provider's key, the app opens on the key screen; after "choose later", a band.
  const keyScreen = state === 'open' && keyScreenShown(options, later, notice)
  const keyBand = state === 'open' && later && keyScreenShown(options, false, null)
  // The rails' widths, which styles.css gives the sidebar and the side panel.
  const panelWidth = useStore($panelWidth)
  const sidebarWidth = useStore($sidebarWidth)
  const widths = {
    '--panel-width': `${String(panelWidth)}px`,
    '--sidebar-width': `${String(sidebarWidth)}px`
  } as CSSProperties

  // Whether the server's machine opens terminals, asked each time a connection opens (another
  // machine's may not); Ctrl+` shows or hides it, Ctrl+B the sidebar.
  useEffect(() => {
    if (state === 'open') {
      void refreshTerminalAvailable()
    }
  }, [state, session])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'b') {
        event.preventDefault()
        toggleSidebar()
      }

      if (event.ctrlKey && event.key === '`') {
        event.preventDefault()

        if ($terminals.get().length === 0) {
          void terminalAvailable().then(found => (found ? openTerminal(currentFolder()) : undefined))
        } else {
          togglePanel()
        }
      }
    }
    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  return (
    <div
      className="relative flex h-full bg-white text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100"
      data-connection={state}
      style={widths}
    >
      <Sidebar />
      <main className="main-pane flex min-w-0 flex-1 flex-col">
        <ConnectionBand />
        {keyBand && <KeyBand />}
        {keyScreen ? (
          <KeyScreen />
        ) : view === 'proposals' ? (
          <ProposalsView />
        ) : view === 'routines' ? (
          <RoutinesView />
        ) : view === 'plugin' ? (
          <PluginPage />
        ) : view === 'session' ? (
          <SessionView />
        ) : (
          <>
            {bot && <BotHeader bot={bot} />}
            <ThreadView empty={bot ? <BotEmpty bot={bot} /> : undefined} thread={thread} />
            {shown.length + proposed.length + done.length > 0 && (
              <div className="mx-auto max-h-[50vh] w-full max-w-3xl space-y-3 overflow-y-auto px-6 pb-3">
                {shown.map(card => {
                  if (card.kind === 'ask') {
                    return <AskCard card={card} key={card.id} />
                  }
                  if (card.kind === 'secret') {
                    return <SecretCard card={card} key={card.id} />
                  }

                  return <ApprovalCard card={card} key={card.id} />
                })}
                {done.map(({ proposal, result }) => (
                  <ProposalCard approved={result} key={proposal.id} proposal={proposal} />
                ))}
                {proposed.map(proposal => (
                  <ProposalCard key={proposal.id} proposal={proposal} />
                ))}
              </div>
            )}
            <PreviewLinks thread={thread} />
            <Composer connected={state === 'open'} running={thread.running} to={bot?.title} />
          </>
        )}
      </main>
      <SidePanel />
      <BotDialogHost />
      <HomeDialogHost />
      <SettingsView />
    </div>
  )
}

/** A band while the connection is not open, with why the last dial failed. */
function ConnectionBand() {
  const { error, state } = useStore($connection)

  if (state === 'open') {
    return null
  }

  const text = state === 'connecting' || state === 'idle' ? t.connection.connecting : t.connection.closed

  return (
    <div
      className="border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
      data-testid="connection-band"
      role="status"
    >
      {text}
      {error && <span className="ml-2 opacity-75">{t.connection.failed(error)}</span>}
    </div>
  )
}
