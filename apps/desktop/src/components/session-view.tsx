import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import type {
  AccountRow,
  AccountSwitchRow,
  ExecutorModel,
  ExecutorRow,
  InstanceRow,
  RouteTargetParams
} from '@/protocol/protocol.generated'
import {
  $cards,
  $connection,
  $executorAccounts,
  $executorModels,
  $openSession,
  $remote,
  $sessionError,
  $sessionFolders,
  $sessionRoute,
  $sessionSettings,
  $sessionThread,
  $slashCommands,
  $workbench,
  checkSessionAccount,
  chooseElsewhere,
  type OpenSession,
  readExecutorCommands,
  readSessionRoute,
  runUserCommand,
  sendToSession,
  sessionOwner,
  setSessionModel,
  setSessionRoute,
  setSessionSwitch,
  slashKey,
  stopSession,
  switchSessionAccount
} from '@/store/app'
import { fromNames } from '@/store/slash'
import { EMPTY_THREAD } from '@/store/thread'
import { waitingCalls } from '@/store/waiting'

import { type ExecutorCard, ExecutorCardView } from './executor-cards'
import { baseName } from './executors-list'
import { HarnessAutoWarning, useHarnessAuto } from './harness-auto'
import { MessageBox } from './message-box'
import { TerminalButton } from './side-panel'
import { ThreadView } from './thread-view'
import { ArrowDown, ArrowUp, Close, Route, VsCode } from './ui/icons'
import { PILL, PILL_ON, PillSelect } from './ui/pill'
import { IconButton } from './ui/tip'

/**
 * An executor's session as a conversation (step 16f, decided with the user on 15/10/2026): the
 * trail of Atlas's own (step 9c), what its harness kept and what it does live. The workbench card
 * of step 16c comes apart into the usual places: its prompt is the box, which steers while it
 * works; its model and effort sit above the box and its automatic mode is "Auto", as in Atlas's
 * conversation (one box for every conversation, asked for by the user on 16/10/2026, when
 * "Atlas answers" left the screen); what it asks shows in the conversation; it stops from the box,
 * in send's place while it works. Its account is a pill beside its model (step 16h): switching goes
 * on with the conversation in a copy on the other account, after the user read what changes; each
 * account's login switches from its row in the sidebar.
 */
export function SessionView() {
  const open = useStore($openSession)
  const thread = useStore($sessionThread)
  const cards = useStore($cards)
  const { executors, instances } = useStore($workbench)
  const error = useStore($sessionError)

  if (!open) {
    return null
  }

  const instance = open.instanceId ? instances.find(row => row.instance_id === open.instanceId) : undefined
  const live = instance !== undefined && instance.state !== 'ended'
  const executor = executors.find(row => row.name === open.executor)
  const title = executor?.title ?? open.executor
  const asked = cards.filter(
    (card): card is ExecutorCard =>
      (card.kind === 'executor-approval' || card.kind === 'executor-question') &&
      live &&
      card.params.instance_id === open.instanceId
  )
  const elsewhere = !live && open.elsewhere !== '' && open.choice === null
  const readOnly = open.expired || open.choice === 'read'

  return (
    <>
      <SessionHeader account={instance?.account || executor?.account || ''} open={open} title={title} />
      {elsewhere && <Elsewhere where={open.elsewhere} />}
      <ThreadView
        empty={
          <p className="pt-24 text-center text-neutral-500">
            {open.sessionId ? '' : t.executors.emptySession(title, baseName(open.folder))}
          </p>
        }
        // A session kept by its harness is read as it opens: never shown as an empty one meanwhile.
        loading={open.sessionId !== '' && thread === EMPTY_THREAD && !error}
        place={sessionOwner(open)}
        thread={thread}
        waiting={waitingCalls(asked, thread.items)}
      />
      {(asked.length > 0 || error) && (
        <div className="mx-auto max-h-[50vh] w-full max-w-3xl space-y-3 overflow-y-auto px-6 pb-3">
          {asked.map(card => (
            <ExecutorCardView card={card} key={card.id} />
          ))}
          {error && (
            <p className="text-sm text-red-600" data-testid="session-error">
              {error}
            </p>
          )}
        </div>
      )}
      {readOnly ? (
        <p
          className="border-t border-neutral-200 p-4 text-center text-sm text-neutral-500 dark:border-neutral-800"
          data-testid="session-read-only"
        >
          {open.expired ? t.executors.expired(title) : t.executors.readOnly}
        </p>
      ) : (
        !elsewhere && <SessionComposer instance={live ? instance : undefined} open={open} title={title} />
      )}
    </>
  )
}

/** The session's title and where it works; switching account is the executor's, in the sidebar,
 * and stopping is the box's (asked for by the user on 18/10/2026). Beside them, its folder opens in
 * VS Code, to read the project's code (asked for by the user on 18/10/2026), where the folder is on
 * this machine. */
function SessionHeader({ account, open, title }: { account: string; open: OpenSession; title: string }) {
  const [failed, setFailed] = useState('')
  const local = !useStore($remote) && open.folder !== ''

  return (
    <header className="border-b border-neutral-200 px-6 py-3 dark:border-neutral-800" data-testid="session-header">
      <div className="mx-auto flex max-w-3xl items-center gap-2">
        <div className="min-w-0 flex-1">
          <h1 className="truncate font-semibold">{open.title || t.executors.untitled}</h1>
          <p className="truncate text-xs text-neutral-500" title={open.folder}>
            {[title, baseName(open.folder), account].filter(Boolean).join(' · ')}
          </p>
          {failed && (
            <p className="truncate text-xs text-red-600" data-testid="open-in-editor-error" title={failed}>
              {failed}
            </p>
          )}
        </div>
        {local && (
          <IconButton
            icon={VsCode}
            label={t.executors.openInEditor}
            onClick={() => {
              setFailed('')
              void window.atlas.openInEditor(open.folder).then(setFailed)
            }}
            side="bottom"
            testId="open-in-editor"
          />
        )}
      </div>
    </header>
  )
}

/** The session is open in another program (decided with the user on 15/10/2026): a warning, with
 * the ways out. */
function Elsewhere({ where }: { where: string }) {
  const place = t.executors.places[where] ?? where

  return (
    <div className="mx-auto mt-4 w-full max-w-3xl px-6" data-testid="session-elsewhere">
      <div className="space-y-2 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950">
        <p className="font-semibold">⚠ {t.executors.elsewhereTitle(place)}</p>
        <p className="text-neutral-700 dark:text-neutral-300">{t.executors.elsewhereBody}</p>
        <div className="flex flex-wrap gap-2">
          <button
            className="rounded border border-neutral-300 bg-white px-3 py-1 dark:border-neutral-700 dark:bg-neutral-900"
            onClick={() => {
              chooseElsewhere('read')
            }}
            type="button"
          >
            {t.executors.elsewhereRead}
          </button>
          <button
            className="rounded bg-orange-600 px-3 py-1 font-medium text-white"
            onClick={() => {
              chooseElsewhere('copy')
            }}
            type="button"
          >
            {t.executors.elsewhereCopy}
          </button>
          <button
            className="rounded border border-neutral-300 bg-white px-3 py-1 dark:border-neutral-700 dark:bg-neutral-900"
            onClick={() => {
              chooseElsewhere('here')
            }}
            type="button"
          >
            {t.executors.elsewhereHere}
          </button>
        </div>
      </div>
    </div>
  )
}

/** The session's box, the one `MessageBox`: a prompt starts a turn, or steers the one running; `/`
 * offers the commands its harness says its channel takes (Claude Code's `slash_commands`); "Auto"
 * is the harness's own automatic mode, where Atlas's conversation has its own, on only after its
 * warning is read and confirmed. It takes files as Atlas's box does when its executor says it
 * does (step 16i): the same chips, sent with the prompt. */
function SessionComposer({
  instance,
  open,
  title
}: {
  instance: InstanceRow | undefined
  open: OpenSession
  title: string
}) {
  const { executors, instances } = useStore($workbench)
  const executor = executors.find(row => row.name === open.executor)
  const settings = useStore($sessionSettings)
  const { state } = useStore($connection)
  const working = instance?.state === 'working'
  // As its harness describes them in this folder, read when the session opens and again when its
  // instance's commands change; meanwhile the names its card says, or the last instance's.
  const names =
    instance?.commands ??
    instances.findLast(row => row.executor === open.executor && row.commands.length)?.commands ??
    []
  const described = useStore($slashCommands)[slashKey(open.executor, open.folder)]
  const commands = described ?? fromNames(names)
  const signature = instance?.commands.join(' ') ?? ''
  const instanceId = instance?.instance_id ?? ''

  useEffect(() => {
    readExecutorCommands(open.executor, open.folder, instanceId, signature)
  }, [open.executor, open.folder, instanceId, signature])
  const auto = useHarnessAuto(on => void setSessionSwitch('auto', on))
  const [switching, setSwitching] = useState<null | Switching>(null)
  const [routing, setRouting] = useState(false)
  const pick = (account: string, name: string) => {
    setRouting(false)
    setSwitching({ account, check: null, name })
    void checkSessionAccount(account).then(check => {
      setSwitching(check ? { account, check, name } : null)
    })
  }

  return (
    <MessageBox
      auto={
        (instance?.can_auto ?? true)
          ? {
              label: t.executors.auto,
              on: instance ? instance.auto : settings.auto,
              set: auto.toggle,
              title: t.executors.autoNote
            }
          : undefined
      }
      blocked={working && !instance.steer}
      commands={commands}
      connected={state === 'open'}
      // A new session's words are its folder's until it has an id.
      draftKey={open.sessionId || open.instanceId ? sessionOwner(open) : `session-new:${open.executor}:${open.folder}`}
      label={t.executors.placeholder(title)}
      note={
        auto.asking ? (
          <HarnessAutoWarning onCancel={auto.cancel} onConfirm={auto.confirm} title={title} />
        ) : switching ? (
          <AccountSwitchNote
            onCancel={() => {
              setSwitching(null)
            }}
            onConfirm={() => {
              setSwitching(null)
              void switchSessionAccount(
                switching.account,
                t.accounts.switched(switching.name, switching.check?.tokens ?? null)
              )
            }}
            switching={switching}
          />
        ) : routing ? (
          <SessionAccountRouteEditor
            instance={instance}
            onClose={() => {
              setRouting(false)
            }}
            onPick={pick}
            open={open}
          />
        ) : open.choice === 'copy' && !instance ? (
          <p className="text-xs text-orange-700" data-testid="session-copy-note">
            {t.executors.copyNote}
          </p>
        ) : undefined
      }
      onCommand={command => void runUserCommand(command)}
      onSend={(text, attachments) => sendToSession(text, attachments)}
      onStop={working ? () => void stopSession() : undefined}
      owner={executor?.files ? sessionOwner(open) : undefined}
      placeholder={working ? t.executors.placeholderWorking : t.executors.placeholder(title)}
      testId="session-box"
      toolbar={
        <>
          <SessionAccountRoute
            instance={instance}
            on={routing}
            onToggle={() => {
              setRouting(!routing)
            }}
            open={open}
          />
          <SessionModelPicker instance={instance} open={open} />
          <TerminalButton />
        </>
      }
    />
  )
}

/** The session's account and route as one pill by its box (asked for by the user on 22/10/2026,
 * where each had its own, the route's first stop being the account): the account it runs on, once
 * its executor has another, and how many destinations its route has (asked for by the user on
 * 21/10/2026); pressed, both are chosen above the box. */
function SessionAccountRoute({
  instance,
  on,
  onToggle,
  open
}: {
  instance: InstanceRow | undefined
  on: boolean
  onToggle: () => void
  open: OpenSession
}) {
  const accounts = useSessionAccounts(instance, open)
  const route = useStore($sessionRoute)
  const { state } = useStore($connection)
  const instanceId = instance?.instance_id ?? ''

  useEffect(() => {
    if (state === 'open' && instanceId) {
      void readSessionRoute(instanceId)
    }
  }, [instanceId, state])

  if (!accounts && !instanceId) {
    return null
  }

  const count = instanceId && route?.instance_id === instanceId && route.enabled ? route.stops.length : 0

  return (
    <button
      aria-pressed={on}
      className={`${PILL} ${on ? PILL_ON : ''}`}
      data-testid="session-route"
      disabled={state !== 'open'}
      onClick={onToggle}
      title={accounts ? t.route.titleOn : t.route.title}
      type="button"
    >
      <Route aria-hidden size={14} stroke={1.75} />
      {accounts ? t.route.pillOn(accounts.named(accounts.chosen), count) : t.route.pill(count)}
    </button>
  )
}

/** What the pill opens above the box: the session's account, then its route once it runs. It
 * closes as a popover does (asked for by the user on 22/10/2026): a click outside it, Escape, or
 * its own button; the pill still toggles it. */
function SessionAccountRouteEditor({
  instance,
  onClose,
  onPick,
  open
}: {
  instance: InstanceRow | undefined
  onClose: () => void
  onPick: (account: string, name: string) => void
  open: OpenSession
}) {
  const accounts = useSessionAccounts(instance, open)
  const { state } = useStore($connection)
  const locked = state !== 'open' || instance?.state === 'working'
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const target = event.target

      if (
        target instanceof Node &&
        !panel.current?.contains(target) &&
        !(target instanceof Element && target.closest('[data-testid="session-route"]'))
      ) {
        onClose()
      }
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('pointerdown', outside)
    window.addEventListener('keydown', escape)

    return () => {
      document.removeEventListener('pointerdown', outside)
      window.removeEventListener('keydown', escape)
    }
  }, [onClose])

  return (
    <div className="relative space-y-3 pr-7" data-testid="session-route-panel" ref={panel}>
      <IconButton
        className="absolute top-0 right-0"
        icon={Close}
        label={t.route.close}
        onClick={onClose}
        testId="session-route-close"
      />
      {accounts && (
        <fieldset className="space-y-1 text-xs" data-testid="session-account">
          <legend className="pb-1 font-medium">{t.accounts.sessionTitle}</legend>
          {accounts.listed.map(found => (
            <label className="flex items-center gap-2" key={found.account_id}>
              <input
                checked={found.account_id === accounts.chosen}
                data-account={found.account_id}
                data-testid="session-account-choice"
                disabled={locked}
                name="session-account"
                onChange={() => {
                  onPick(found.account_id, accounts.named(found.account_id))
                }}
                type="radio"
              />
              {found.account_id
                ? found.nickname
                : found.login
                  ? `${found.login} · ${t.accounts.main}`
                  : t.accounts.mainAccount}
            </label>
          ))}
          <p className="text-neutral-500">{locked ? t.accounts.sessionBusy : t.accounts.session}</p>
        </fieldset>
      )}
      {instance && <SessionRouteEditor instanceId={instance.instance_id} />}
    </div>
  )
}

/** A destination the route may take: an executor on one of its accounts, as its row names it. */
interface RouteChoice {
  key: string
  label: string
  target: RouteTargetParams
}

/** The executors' rows as destinations: each harness on each of its accounts, on and installed. */
function routeChoices(executors: ExecutorRow[]): RouteChoice[] {
  return executors
    .filter(row => row.installed && row.enabled)
    .map(row => {
      const executor = row.base || row.name

      return {
        key: row.name,
        label: row.base ? row.title : `${row.title} · ${t.route.main}`,
        target: { account_id: row.account_id, executor }
      }
    })
}

/**
 * The session's route (asked for by the user on 21/10/2026): the destinations it goes on to when
 * the one it runs on reaches its plan's limit, in the user's order, accounts of its own harness
 * and of others; which take part, their order, and whether it is followed. Each change is saved
 * at once, for the session's card.
 */
function SessionRouteEditor({ instanceId }: { instanceId: string }) {
  const route = useStore($sessionRoute)
  const { executors } = useStore($workbench)

  if (route?.instance_id !== instanceId) {
    return (
      <p className="text-xs text-neutral-500" role="status">
        …
      </p>
    )
  }

  const chosen: RouteChoice[] = route.stops.map(stop => ({
    key: stop.name,
    label: `${stop.nickname ? `${stop.title} · ${stop.nickname}` : `${stop.title} · ${t.route.main}`}${stop.installed ? '' : ` (${t.route.notInstalled})`}`,
    target: { account_id: stop.account_id, executor: stop.executor }
  }))
  const others = routeChoices(executors).filter(choice => !chosen.some(found => found.key === choice.key))
  const save = (changed: RouteChoice[], enabled: boolean): void => {
    void setSessionRoute(
      instanceId,
      changed.map(choice => choice.target),
      enabled
    )
  }
  const move = (index: number, by: number): void => {
    const changed = [...chosen]
    const [row] = changed.splice(index, 1)

    if (row) {
      changed.splice(index + by, 0, row)
      save(changed, route.enabled)
    }
  }

  return (
    <div className="space-y-1 text-xs" data-testid="session-route-editor">
      <p className="font-medium">{t.route.title}</p>
      <label className="flex items-center gap-2">
        <input
          checked={route.enabled}
          data-testid="session-route-enabled"
          onChange={event => {
            save(chosen, event.target.checked)
          }}
          type="checkbox"
        />
        {t.route.enabled}
      </label>
      <p className="text-neutral-500">{chosen.length ? t.route.note : t.route.empty}</p>
      <ol className="space-y-0.5" data-testid="session-route-stops">
        {chosen.map((choice, index) => (
          <li className="flex items-center gap-1" data-testid="session-route-stop" key={choice.key}>
            <input
              aria-label={t.route.remove(choice.label)}
              checked
              onChange={() => {
                save(
                  chosen.filter(found => found.key !== choice.key),
                  route.enabled
                )
              }}
              type="checkbox"
            />
            <span className="w-4 text-neutral-400">{index + 1}.</span>
            <span className="min-w-0 flex-1 truncate">{choice.label}</span>
            <IconButton
              disabled={index === 0}
              icon={ArrowUp}
              label={t.route.up(choice.label)}
              onClick={() => {
                move(index, -1)
              }}
            />
            <IconButton
              disabled={index === chosen.length - 1}
              icon={ArrowDown}
              label={t.route.down(choice.label)}
              onClick={() => {
                move(index, 1)
              }}
            />
          </li>
        ))}
      </ol>
      {others.length > 0 && (
        <ul className="space-y-0.5 text-neutral-500" data-testid="session-route-others">
          {others.map(choice => (
            <li className="flex items-center gap-1" key={choice.key}>
              <input
                aria-label={t.route.add(choice.label)}
                checked={false}
                onChange={() => {
                  save([...chosen, choice], route.enabled)
                }}
                type="checkbox"
              />
              <span className="min-w-0 flex-1 truncate pl-5">{choice.label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** A switch of the session's account asked for, while what changes is read and then shown. */
interface Switching {
  account: string
  /** `null` while it is read. */
  check: AccountSwitchRow | null
  name: string
}

/**
 * The session's accounts (step 16h, decided with the user on 18/10/2026 and redone on 19/10/2026),
 * once its executor has another one: the main account by its login, then the others by their
 * nicknames. A pick with an instance running is confirmed first (`AccountSwitchNote`); with none,
 * the next instance starts on it. Before a pick, a session not started shows its folder's account.
 * `null`, with only one.
 */
function useSessionAccounts(
  instance: InstanceRow | undefined,
  open: OpenSession
): null | { chosen: string; listed: AccountRow[]; named: (account: string) => string } {
  const { executors } = useStore($workbench)
  const row = executors.find(found => found.name === open.executor)
  const base = row?.base || open.executor
  const read = useStore($executorAccounts)[base]
  const settings = useStore($sessionSettings)
  const folders = useStore($sessionFolders)[open.executor]?.folders
  const listed = read?.accounts ?? []

  if (listed.length < 2) {
    return null
  }

  const live = instance !== undefined && instance.state !== 'ended' && instance.state !== 'failed'
  const folder = folders?.find(found => found.folder === open.folder)?.account_id ?? ''
  const chosen = live ? instance.account_id : (settings.account ?? (row?.account_id || folder))
  const named = (account: string) => {
    const found = listed.find(found => found.account_id === account)

    return found?.account_id ? found.nickname : found?.login || t.accounts.mainAccount
  }

  return { chosen, listed, named }
}

/** What a switch of account changes, for the user to read before it happens (decided with the user
 * on 19/10/2026): the account that gets the conversation, the tools it lacks, the copy, and the
 * first message read again on its plan. */
function AccountSwitchNote({
  onCancel,
  onConfirm,
  switching
}: {
  onCancel: () => void
  onConfirm: () => void
  switching: Switching
}) {
  const { check, name } = switching
  const who = check?.login ? `${name} (${check.login})` : name

  return (
    <div className="space-y-1 text-xs" data-testid="account-switch" role="alertdialog">
      <p className="font-medium">{t.accounts.switchTitle(who)}</p>
      {check === null ? (
        <p className="text-neutral-500" role="status">
          {t.accounts.switchChecking}
        </p>
      ) : (
        <>
          <p className="text-neutral-600 dark:text-neutral-300">{t.accounts.switchCopy}</p>
          {check.missing.length > 0 && (
            <p className="text-amber-700 dark:text-amber-500" data-testid="account-switch-missing">
              {t.accounts.switchMissing(check.missing.join(', '))}
            </p>
          )}
          {!check.compared && <p className="text-neutral-500">{t.accounts.switchUnknown}</p>}
          <p className="text-neutral-600 dark:text-neutral-300">{t.accounts.switchCost(check.tokens ?? null)}</p>
        </>
      )}
      <div className="flex gap-2">
        <button
          className="rounded bg-orange-600 px-2 py-0.5 font-medium text-white disabled:opacity-50"
          data-testid="account-switch-confirm"
          disabled={check === null}
          onClick={onConfirm}
          type="button"
        >
          {t.accounts.switchGo}
        </button>
        <button className="rounded px-2 py-0.5 text-neutral-500" onClick={onCancel} type="button">
          {t.accounts.cancel}
        </button>
      </div>
    </div>
  )
}

/**
 * The session's model and effort, above its box where Atlas's conversation has its own (a model
 * pill and an effort pill): the models its harness's `/model` lists for the account, and the
 * efforts of the one chosen, gone for a model that takes none. A pick applies from the instance's
 * next request, or to the instance the next prompt starts; the first, the harness's default model
 * on Atlas's default effort. Both show what runs, never "Default" (asked for by the user on
 * 17/10/2026): the harness's default model by the name of the model it runs as, and the defaults
 * marked in the lists.
 */
function SessionModelPicker({ instance, open }: { instance: InstanceRow | undefined; open: OpenSession }) {
  const offered = useStore($executorModels)[open.executor]
  const settings = useStore($sessionSettings)
  const { state } = useStore($connection)

  if (!offered?.length || (instance && !instance.can_model)) {
    return null
  }

  const chosen = instance ? instance.chosen_model || null : settings.model
  const offer = modelOffer(offered, chosen, instance?.model ?? '')
  const effort = (instance ? instance.effort : settings.effort) || offer.current.default_effort || null

  const pickModel = (id: string) => {
    const next = offer.options.find(option => option.model.id === id)?.model
    // An effort the new model does not take goes back to Atlas's default for it.
    const kept = effort && next?.efforts.includes(effort) ? effort : null
    void setSessionModel(id === offered[0]?.id ? null : id, kept)
  }

  return (
    <div className="flex items-center gap-2">
      <PillSelect
        aria-label={t.executors.model}
        data-testid="session-model"
        disabled={state !== 'open'}
        onChange={event => {
          pickModel(event.target.value)
        }}
        title={offer.current.description || t.executors.model}
        value={offer.current.id}
      >
        {offer.options.map(({ label, model }) => (
          <option key={model.id} title={model.description} value={model.id}>
            {label}
          </option>
        ))}
      </PillSelect>
      {offer.current.efforts.length > 0 && (
        <PillSelect
          aria-label={t.executors.effort}
          data-testid="session-effort"
          disabled={state !== 'open'}
          onChange={event => {
            void setSessionModel(chosen, event.target.value || null)
          }}
          title={t.executors.effort}
          value={effort ?? ''}
        >
          {effort === null && <option value="" />}
          {offer.current.efforts.map(level => (
            <option key={level} value={level}>
              {level === offer.current.default_effort
                ? t.composer.defaulted(t.composer.effortLevel(level))
                : t.composer.effortLevel(level)}
            </option>
          ))}
        </PillSelect>
      )}
      {offer.ranAs && (
        <span className="text-neutral-500" data-testid="session-ran-as">
          {t.executors.runsAs(offer.ranAs)}
        </span>
      )}
    </div>
  )
}

/** What the session's picker shows: the model chosen (the harness's default, the first, when none;
 * one the list lacks, added), and what its last turn ran as when that is not what the pick names
 * (a model from the user's own settings, say). The default goes by the name of the model it runs
 * as, marked, in the place of the other entry for that model (Claude Code's "Default
 * (recommended)" is "Opus 5.5 · default", and its own "Opus 5.5" is not listed twice). */
export function modelOffer(
  models: ExecutorModel[],
  chosen: null | string,
  ran: string
): { current: ExecutorModel; options: { label: string; model: ExecutorModel }[]; ranAs: string } {
  const found = models.find(model => model.id === chosen)
  const extra = (id: string): ExecutorModel => ({
    default_effort: '',
    description: '',
    efforts: [],
    id,
    name: id,
    resolved: ''
  })
  const current = found ?? (chosen ? extra(chosen) : models[0]) ?? extra('')
  const listed = models.includes(current) ? models : [...models, current]
  const first = models[0]
  const same = (model: ExecutorModel) =>
    first !== undefined && model !== first && first.resolved !== '' && model.resolved === first.resolved
  const named = models.find(same)?.name ?? first?.name ?? ''
  const options = listed
    .filter(model => !same(model) || model === current)
    .map(model => ({ label: model === first ? t.composer.defaulted(named) : model.name, model }))
  const ranAs = ran && ran !== current.resolved && ran !== current.id ? ran : ''

  return { current, options, ranAs }
}
