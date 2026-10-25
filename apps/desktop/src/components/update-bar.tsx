import { useStore } from '@nanostores/react'
import { Dialog } from 'radix-ui'
import { useState } from 'react'

import { type Locale, locale, t } from '@/i18n'
import { $session, workUnderway } from '@/store/app'
import {
  $confirmUnverified,
  $update,
  $updateEnded,
  $updateError,
  $updateLog,
  $updateOpen,
  $updating,
  applyUpdate,
  dismissUpdateEnded,
  openUpdateLog
} from '@/store/updates'

import { Close, Update } from './ui/icons'
import { ICON_BUTTON, IconButton } from './ui/tip'

/**
 * The app's update, at the foot of the sidebar above the home (asked for by the user on
 * 18/10/2026): a button only when `main` has commits this installation lacks, opening what they are
 * and "Update and reopen", which closes the app, updates it and opens it again by itself; and,
 * once the app opened again, how the update went.
 *
 * The same button, with another machine's server on screen, updates that server (store/updates.ts):
 * "Update and reconnect", since a server restarts where the app would close, and the end is said
 * as this computer's is, once the app reconnects.
 */
export function UpdateBar() {
  const update = useStore($update)
  const ended = useStore($updateEnded)

  if (!ended && !update?.available) {
    return null
  }

  return (
    <div className="space-y-2 px-2 pt-2" data-testid="update-bar">
      {ended && <Ended ended={ended} />}
      {update?.available && (
        <button
          className="flex w-full items-center gap-2 rounded-md bg-sky-600 px-2 py-1.5 text-left text-sm font-medium text-white hover:bg-sky-700"
          data-testid="update-button"
          onClick={() => {
            $updateOpen.set(true)
          }}
          type="button"
        >
          <Update aria-hidden size={16} stroke={1.75} />
          <span className="min-w-0 flex-1 truncate">{t.updates.button}</span>
          <span className="rounded-full bg-white/25 px-1.5 text-xs leading-4" data-testid="update-count">
            {update.behind}
          </span>
        </button>
      )}
      <UpdateDialog />
    </div>
  )
}

/** The sidebar's strip, hidden: the update still shows, as a button that opens its dialog. */
export function UpdateStrip() {
  const update = useStore($update)

  if (!update?.available) {
    return null
  }

  return (
    <>
      <IconButton
        className="text-sky-600 dark:text-sky-400"
        icon={Update}
        label={t.updates.button}
        onClick={() => {
          $updateOpen.set(true)
        }}
        side="right"
        testId="update-strip"
      />
      <UpdateDialog />
    </>
  )
}

/** The commits' subjects in the screen's language: in Portuguese, a commit's `Notes-pt-BR` line,
 * where it has one (AGENTS.md, Commits); English where it has none. */
export function subjectsIn(update: UpdateStatus, language: Locale): string[] {
  return update.commits.map((subject, index) => (language === 'pt-BR' ? update.commits_pt_br[index] : '') || subject)
}

/** What a server's restart stops is that server's; this computer's app closing, its own homes'. */
function underwayFor(update: null | UpdateStatus) {
  const session = $session.get()

  return workUnderway(update?.kind === 'compose' && session ? session : undefined)
}

/** What the update brings, what closing now stops, and the update itself. */
function UpdateDialog() {
  const update = useStore($update)
  const open = useStore($updateOpen)
  const updating = useStore($updating)
  const error = useStore($updateError)
  const confirmed = useStore($confirmUnverified)
  const [underway, setUnderway] = useState(() => underwayFor(update))

  if (!update?.available) {
    return null
  }

  // A server's update: it restarts rather than closing the app, and goes on if the dialog closes.
  const server = update.kind === 'compose'
  const unverified = server ? (update.unverified ?? '') : ''
  const more = update.behind - update.commits.length
  const subjects = subjectsIn(update, locale)
  const stops = [
    underway.turns && t.updates.turns(underway.turns),
    underway.subagents && t.updates.subagents(underway.subagents),
    underway.executors && t.updates.executors(underway.executors)
  ].filter(Boolean)
  const label = updating
    ? server
      ? t.updates.updatingServer
      : t.updates.closing
    : server
      ? t.updates.applyServer
      : t.updates.apply

  return (
    <Dialog.Root
      onOpenChange={next => {
        // What runs is read as it opens; nothing is taken from it while the app closes.
        if (next) {
          setUnderway(underwayFor(update))
        }

        if (!updating || server) {
          $updateOpen.set(next)
        }
      }}
      open={open}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed top-1/2 left-1/2 z-50 flex max-h-[80vh] w-[28rem] -translate-x-1/2 -translate-y-1/2 flex-col gap-3 rounded-xl border border-neutral-200 bg-white p-5 text-sm text-neutral-900 shadow-xl dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100"
          data-testid="update-dialog"
        >
          <div className="flex items-start justify-between">
            <Dialog.Title className="font-semibold">{t.updates.title}</Dialog.Title>
            {(!updating || server) && (
              <Dialog.Close aria-label={t.updates.later} className={ICON_BUTTON}>
                <Close aria-hidden size={16} stroke={1.75} />
              </Dialog.Close>
            )}
          </div>
          <p className="text-neutral-600 dark:text-neutral-400">{t.updates.brings(update.behind)}</p>
          <ul className="min-h-0 list-disc space-y-1 overflow-y-auto pl-5" data-testid="update-commits">
            {subjects.map((subject, index) => (
              <li key={`${String(index)}-${subject}`}>{subject}</li>
            ))}
            {more > 0 && <li className="list-none text-neutral-500">{t.updates.more(more)}</li>}
          </ul>
          <p className="text-neutral-600 dark:text-neutral-400">{server ? t.updates.howServer : t.updates.how}</p>
          {stops.length > 0 && (
            <p
              className="rounded-md bg-amber-50 px-2 py-1.5 text-amber-900 dark:bg-amber-950 dark:text-amber-200"
              data-testid="update-underway"
            >
              {server ? t.updates.stopsServer(stops.join(', ')) : t.updates.stops(stops.join(', '))}
            </p>
          )}
          {unverified && (
            <div
              className="space-y-1.5 rounded-md bg-amber-50 px-2 py-1.5 text-amber-900 dark:bg-amber-950 dark:text-amber-200"
              data-testid="update-unverified"
            >
              <p>{t.updates.unverified(unverified)}</p>
              <label className="flex items-center gap-2">
                <input
                  checked={confirmed}
                  data-testid="update-confirm-unverified"
                  disabled={updating}
                  onChange={event => {
                    $confirmUnverified.set(event.target.checked)
                  }}
                  type="checkbox"
                />
                {t.updates.confirmUnverified}
              </label>
            </div>
          )}
          {update.dirty && (
            <p className="rounded-md bg-red-50 px-2 py-1.5 text-red-800 dark:bg-red-950 dark:text-red-200">
              {t.updates.dirty}
            </p>
          )}
          {error && (
            <p
              className="rounded-md bg-red-50 px-2 py-1.5 text-red-800 [overflow-wrap:anywhere] dark:bg-red-950 dark:text-red-200"
              role="alert"
            >
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            {(!updating || server) && (
              <Dialog.Close className="rounded-md border border-neutral-300 px-3 py-1.5 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800">
                {t.updates.later}
              </Dialog.Close>
            )}
            <button
              className="rounded-md bg-sky-600 px-3 py-1.5 font-medium text-white hover:bg-sky-700 disabled:opacity-50"
              data-testid="update-apply"
              disabled={updating || update.dirty || (unverified !== '' && !confirmed)}
              onClick={() => {
                void applyUpdate()
              }}
              type="button"
            >
              {label}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

/** How the last update went, once the app opened again, or once it reconnected to the server. */
function Ended({ ended }: { ended: HandedOff }) {
  const text = ended.ok ? t.updates.done(ended.commits.length) : t.updates.failed(ended.error)
  const log = useStore($updateLog)

  return (
    <div
      className={`flex items-start gap-2 rounded-md px-2 py-1.5 text-xs [overflow-wrap:anywhere] ${
        ended.ok
          ? 'bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200'
          : 'bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200'
      }`}
      data-ok={ended.ok}
      data-testid="update-ended"
      role={ended.ok ? 'status' : 'alert'}
    >
      <span className="flex-1 space-y-1">
        <span className="block">{text}</span>
        {ended.reopen && <span className="block">{ended.reopen}</span>}
        {!ended.ok && ended.log && (
          <button
            className="underline"
            onClick={() => {
              openUpdateLog()
            }}
            type="button"
          >
            {t.updates.log}
          </button>
        )}
        {log !== null && (
          <pre
            className="max-h-48 overflow-auto rounded bg-black/5 p-1 font-mono text-[10px] whitespace-pre-wrap dark:bg-white/10"
            data-testid="update-log"
          >
            {log}
          </pre>
        )}
      </span>
      <button aria-label={t.updates.dismiss} className="shrink-0" onClick={dismissUpdateEnded} type="button">
        <Close aria-hidden size={12} stroke={2} />
      </button>
    </div>
  )
}
