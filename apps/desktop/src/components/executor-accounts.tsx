import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { ExecutorRow } from '@/protocol/protocol.generated'
import {
  $executorAccounts,
  $workbenchError,
  addAccount,
  removeAccount,
  setFolderAccount,
  showExecutor,
  switchExecutorAccount
} from '@/store/app'

import { Plus } from './ui/icons'

/*
 * An executor's accounts (step 16h, decided with the user on 18/10/2026 and redone on 19/10/2026):
 * each account is the harness's own folder for it, with its own login, settings and sessions, and
 * shows as an executor of its own, `Claude Code · company-b`. "Add an account" sits above the
 * executors' list (asked for by the user on 19/10/2026) and takes only a nickname; each row's menu
 * has its login, which the harness itself does on the user's browser, and an account's its removal,
 * which leaves its folder as it is (asked for by the user on 21/10/2026, where both sat in its tree).
 * Atlas never sees a credential. How they take over from one another at a plan's limit is each
 * session's route (session-view.tsx): the accounts' own order, under the main account's row until
 * the user asked on 22/10/2026, is no longer on screen, and a session with no route keeps it.
 */

/** The switch to another login, opened from the row's menu: the user's sessions on it go on with
 * the new one, never while one works. The email is optional, the browser's login choosing the
 * account. */
export function LoginForm({ executor, onDone }: { executor: ExecutorRow; onDone: () => void }) {
  const [login, setLogin] = useState('')
  const error = useStore($workbenchError)[`account:${executor.name}`]

  return (
    <div className="space-y-1 px-2 pb-1 text-xs" data-testid="executor-account">
      <form
        className="space-y-1"
        onSubmit={event => {
          event.preventDefault()
          void switchExecutorAccount(executor.name, login).then(done => {
            if (done) {
              setLogin('')
              onDone()
            }
          })
        }}
      >
        <p className="text-soft">{t.accounts.loginNote(executor.title)}</p>
        <div className="flex gap-1">
          <input
            aria-label={t.accounts.login}
            autoFocus
            className="min-w-0 flex-1 rounded border border-border-strong px-2 py-1 bg-raised"
            onChange={event => {
              setLogin(event.target.value)
            }}
            placeholder={t.accounts.login}
            value={login}
          />
          <button className="rounded bg-primary px-2 py-1 text-primary-foreground" type="submit">
            {t.accounts.logIn}
          </button>
          <button className="rounded px-2 py-1 text-muted-foreground" onClick={onDone} type="button">
            {t.accounts.cancel}
          </button>
        </div>
      </form>
      {error && (
        <p className="text-failed-text" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

/** A new account, above the executors' list: only its nickname, which names its folder too, and,
 * when more than one harness takes accounts, which one it is of. Its row comes next, to log in. */
export function AddAccount({ executors }: { executors: ExecutorRow[] }) {
  const [adding, setAdding] = useState(false)
  const [nickname, setNickname] = useState('')
  const [chosen, setChosen] = useState('')
  const executor = executors.find(row => row.name === chosen)?.name ?? executors[0]?.name ?? ''
  const error = useStore($workbenchError)[`accounts:${executor}`]

  if (!executor) {
    return null
  }

  if (!adding) {
    return (
      <button
        className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm text-soft hover:bg-muted"
        data-testid="add-account"
        onClick={() => {
          setAdding(true)
        }}
        type="button"
      >
        <Plus aria-hidden size={16} stroke={1.75} />
        {t.accounts.add}
      </button>
    )
  }

  return (
    <form
      autoComplete="off"
      className="space-y-2 rounded-md border border-dashed border-border-strong p-2 text-xs"
      data-testid="add-account-form"
      onSubmit={event => {
        event.preventDefault()
        void addAccount(executor, nickname.trim()).then(done => {
          if (done) {
            setNickname('')
            setAdding(false)
          }
        })
      }}
    >
      {executors.length > 1 && (
        <select
          aria-label={t.accounts.of}
          className="w-full rounded border border-border-strong px-2 py-1 bg-raised"
          data-testid="account-executor"
          onChange={event => {
            setChosen(event.target.value)
          }}
          value={executor}
        >
          {executors.map(row => (
            <option key={row.name} value={row.name}>
              {row.title}
            </option>
          ))}
        </select>
      )}
      <input
        aria-label={t.accounts.nickname}
        autoFocus
        className="w-full rounded border border-border-strong px-2 py-1 bg-raised"
        data-testid="account-nickname"
        maxLength={40}
        onChange={event => {
          setNickname(event.target.value)
        }}
        placeholder={t.accounts.nicknamePlaceholder}
        spellCheck={false}
        value={nickname}
      />
      <p className="text-muted-foreground">{t.accounts.nicknameNote}</p>
      {error && (
        <p className="text-failed-text" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button
          className="rounded bg-primary px-2 py-1 font-medium text-primary-foreground disabled:opacity-50"
          disabled={!nickname.trim()}
          type="submit"
        >
          {t.accounts.adding}
        </button>
        <button
          className="rounded px-2 py-1 text-muted-foreground"
          onClick={() => {
            setNickname('')
            setAdding(false)
          }}
          type="button"
        >
          {t.accounts.cancel}
        </button>
      </div>
    </form>
  )
}

/** An account's removal, asked first once chosen in its row's menu: its folder stays, with its
 * login and its sessions. */
export function RemoveAccount({ executor, onDone }: { executor: ExecutorRow; onDone: () => void }) {
  const listed = useStore($executorAccounts)[executor.base]
  const error = useStore($workbenchError)[`accounts:${executor.base}`]
  const folder = listed?.accounts.find(row => row.account_id === executor.account_id)?.folder ?? ''

  return (
    <div className="space-y-1 px-2 pb-1 text-xs" data-testid="remove-account-confirm">
      <p className="text-soft">{t.accounts.removeNote(executor.nickname, folder)}</p>
      <div className="flex gap-2">
        <button
          className="rounded bg-danger px-2 py-0.5 text-white"
          onClick={() => {
            void removeAccount(executor.base, executor.account_id).then(done => {
              onDone()

              if (done) {
                showExecutor(null)
              }
            })
          }}
          type="button"
        >
          {t.accounts.remove(executor.nickname)}
        </button>
        <button className="rounded px-2 py-0.5 text-muted-foreground" onClick={onDone} type="button">
          {t.accounts.cancel}
        </button>
      </div>
      {error && (
        <p className="text-failed-text" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

/** The account a folder's new sessions start on, beside its name (step 16h): a pill the user
 * changes, shown once the executor has another account. `executor` is the row's: an account's
 * row chooses among its executor's accounts. */
export function FolderAccount({
  executor,
  folder,
  account
}: {
  account: string
  executor: ExecutorRow
  folder: string
}) {
  const base = executor.base || executor.name
  const listed = useStore($executorAccounts)[base]
  const others = listed?.accounts.filter(row => row.account_id) ?? []

  if (others.length === 0) {
    return null
  }

  const name = folder.split(/[\\/]/).filter(Boolean).pop() ?? folder

  return (
    <select
      aria-label={t.accounts.folder(name)}
      className="max-w-24 shrink-0 cursor-pointer appearance-none truncate rounded bg-transparent px-1 text-xs font-normal text-muted-foreground hover:bg-selected"
      data-testid="folder-account"
      onChange={event => {
        void setFolderAccount(base, folder, event.target.value)
      }}
      onClick={event => {
        event.stopPropagation()
      }}
      title={t.accounts.folder(name)}
      value={account}
    >
      <option value="">{t.accounts.main}</option>
      {others.map(row => (
        <option key={row.account_id} value={row.account_id}>
          {row.nickname}
        </option>
      ))}
    </select>
  )
}
