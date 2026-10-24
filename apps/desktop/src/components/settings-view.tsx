import { useStore } from '@nanostores/react'
import { Dialog, Switch } from 'radix-ui'
import { type ReactNode, useState } from 'react'

import { t } from '@/i18n'
import { $threads } from '@/store/app'
import {
  $machines,
  $machinesNotice,
  $machinesWorking,
  $thisMachine,
  addMachine,
  copyCode,
  newToken,
  removeMachine,
  renameMachine,
  setThisMachine,
  testMachine
} from '@/store/machines'
import {
  $settings,
  $settingsSection,
  closeSettings,
  SECTIONS,
  type SettingsSection,
  showSection
} from '@/store/settings'

import { ExecutorOffers } from './executor-offers'
import { PluginsSection } from './plugins-view'
import {
  GeneralSection,
  ModelSection,
  PersonalizationSection,
  SafetySection,
  WorkspaceSection
} from './settings-sections'
import {
  Adjustments,
  Close,
  Computer,
  Copy,
  Cpu,
  Folder,
  Key,
  Machine,
  Plug,
  Plus,
  Shield,
  Sparkles,
  Terminal
} from './ui/icons'
import { ICON_BUTTON } from './ui/tip'

/**
 * The settings screen (step 15b, docs/ARCHITECTURE.md, The settings screen): one large screen with
 * its sections on the left, each opening on
 * the right. General, Model, Personalization, Safety, Workspace, Plugins, Executors (the harnesses on
 * this computer, turned on here, out of the sidebar since 22/10/2026), and Machines, which step 15
 * began with: this machine's home always on and open to the user's
 * machines, with its connection code and a new password; and the other machines, each with Test,
 * Rename and Remove, and Add from a code.
 */
export function SettingsView() {
  const open = useStore($settings)
  const section = useStore($settingsSection)

  return (
    <Dialog.Root
      onOpenChange={next => {
        if (!next) {
          closeSettings()
        }
      }}
      open={open}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed top-1/2 left-1/2 z-50 flex h-[85vh] w-[min(60rem,92vw)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-xl border border-neutral-200 bg-white text-neutral-900 shadow-xl dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100"
          data-testid="settings"
        >
          <nav className="w-52 shrink-0 space-y-0.5 border-r border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950">
            <Dialog.Title className="px-2 pt-1 pb-3 font-semibold">{t.settings.title}</Dialog.Title>
            {SECTIONS.map(each => {
              const Icon = ICONS[each]

              return (
                <button
                  aria-current={each === section ? 'page' : undefined}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-neutral-700 hover:bg-neutral-200 aria-[current=page]:bg-neutral-200 aria-[current=page]:font-medium aria-[current=page]:text-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800 dark:aria-[current=page]:bg-neutral-800 dark:aria-[current=page]:text-neutral-100"
                  data-testid={`settings-nav-${each}`}
                  key={each}
                  onClick={() => {
                    showSection(each)
                  }}
                  type="button"
                >
                  <Icon aria-hidden size={16} stroke={1.75} />
                  {t.settings.sections[each]}
                </button>
              )
            })}
          </nav>
          <div className="min-w-0 flex-1 overflow-y-auto" data-section={section} data-testid="settings-section">
            <div className="flex items-center justify-between px-6 pt-5 pb-3">
              <h2 className="text-lg font-semibold">{t.settings.sections[section]}</h2>
              <Dialog.Close aria-label={t.settings.close} className={ICON_BUTTON}>
                <Close aria-hidden size={16} stroke={1.75} />
              </Dialog.Close>
            </div>
            <div className="space-y-5 px-6 pb-6">
              <Section section={section} />
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

const ICONS = {
  executors: Terminal,
  general: Adjustments,
  machines: Machine,
  model: Cpu,
  personalization: Sparkles,
  plugins: Plug,
  safety: Shield,
  workspace: Folder
} satisfies Record<SettingsSection, unknown>

function Section({ section }: { section: SettingsSection }) {
  switch (section) {
    case 'general':
      return <GeneralSection />
    case 'model':
      return <ModelSection />
    case 'personalization':
      return <PersonalizationSection />
    case 'safety':
      return <SafetySection />
    case 'workspace':
      return <WorkspaceSection />
    case 'plugins':
      return <PluginsSection />
    case 'executors':
      return <ExecutorOffers />
    case 'machines':
      return (
        <>
          <ThisMachine />
          <OtherMachines />
          <Notice />
        </>
      )
  }
}

function Card({ children, testId }: { children: ReactNode; testId: string }) {
  return (
    <section
      className="space-y-3 rounded-lg border border-neutral-200 p-3 dark:border-neutral-800"
      data-testid={testId}
    >
      {children}
    </section>
  )
}

/** This machine's home: always on, open, its code and a new password. */
function ThisMachine() {
  const status = useStore($thisMachine)
  const working = useStore($machinesWorking)
  const threads = useStore($threads)
  const [confirming, setConfirming] = useState(false)
  // Moving the home to its service (or back) drops its server: not under a running turn.
  const busy = Object.values(threads).some(thread => thread.running)
  const off = working || busy || status === null

  return (
    <Card testId="this-machine">
      <p className="flex items-center gap-2 text-sm font-medium">
        <Computer aria-hidden size={16} stroke={1.75} />
        {t.machines.thisMachine(status?.home ?? '…')}
      </p>
      <Toggle
        checked={status?.on ?? false}
        disabled={off}
        hint={t.machines.alwaysOnHint}
        label={t.machines.alwaysOn}
        onChange={on => void setThisMachine(on ? 'on' : 'off')}
        testId="always-on"
      />
      {status?.on && (
        <div className="space-y-3 border-l-2 border-neutral-200 pl-3 dark:border-neutral-800">
          <Toggle
            checked={status.open}
            disabled={off}
            hint={t.machines.openHint}
            label={t.machines.open}
            onChange={open => void setThisMachine(open ? 'open' : 'close')}
            testId="open-to-machines"
          />
          {status.open && status.tailnet && (
            <p className="text-xs text-neutral-500" data-testid="tailnet">
              {status.tailnet.name ? `${status.tailnet.name} · ${status.tailnet.address}` : status.tailnet.address}
            </p>
          )}
          {status.open && status.tailnet_problem && (
            <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="tailnet-problem">
              {t.machines.noTailnet} {status.tailnet_problem}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button
              className={BUTTON}
              data-testid="copy-code"
              disabled={working}
              onClick={() => void copyCode()}
              type="button"
            >
              <Copy aria-hidden size={14} stroke={1.75} />
              {t.machines.copyCode}
            </button>
            {confirming ? (
              <span className="flex items-center gap-2 text-xs">
                {t.machines.newTokenConfirm}
                <button
                  className={BUTTON}
                  data-testid="new-token-confirm"
                  onClick={() => {
                    setConfirming(false)
                    void newToken()
                  }}
                  type="button"
                >
                  {t.machines.newToken}
                </button>
                <button
                  className={BUTTON}
                  onClick={() => {
                    setConfirming(false)
                  }}
                  type="button"
                >
                  {t.machines.cancel}
                </button>
              </span>
            ) : (
              <button
                className={BUTTON}
                data-testid="new-token"
                disabled={working}
                onClick={() => {
                  setConfirming(true)
                }}
                type="button"
              >
                <Key aria-hidden size={14} stroke={1.75} />
                {t.machines.newToken}
              </button>
            )}
          </div>
        </div>
      )}
      {busy && <p className="text-xs text-neutral-500">{t.machines.busy}</p>}
      {working && <p className="text-xs text-neutral-500">{t.machines.working}</p>}
    </Card>
  )
}

function Toggle({
  checked,
  disabled,
  hint,
  label,
  onChange,
  testId
}: {
  checked: boolean
  disabled: boolean
  hint: string
  label: string
  onChange: (checked: boolean) => void
  testId: string
}) {
  return (
    <label className="flex items-start gap-3">
      <Switch.Root
        checked={checked}
        className="relative mt-0.5 h-5 w-9 shrink-0 rounded-full bg-neutral-300 transition-colors disabled:opacity-40 data-[state=checked]:bg-neutral-900 dark:bg-neutral-700 dark:data-[state=checked]:bg-neutral-100"
        data-testid={testId}
        disabled={disabled}
        onCheckedChange={onChange}
      >
        <Switch.Thumb className="block h-4 w-4 translate-x-0.5 rounded-full bg-white transition-transform data-[state=checked]:translate-x-4 dark:data-[state=checked]:bg-neutral-900" />
      </Switch.Root>
      <span>
        <span className="block text-sm">{label}</span>
        <span className="block text-xs text-neutral-500">{hint}</span>
      </span>
    </label>
  )
}

/** The other machines, and Add. */
function OtherMachines() {
  const machines = useStore($machines)
  const [adding, setAdding] = useState(false)

  return (
    <Card testId="other-machines">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">{t.machines.others}</p>
        {!adding && (
          <button
            className={BUTTON}
            data-testid="machine-add"
            onClick={() => {
              setAdding(true)
            }}
            type="button"
          >
            <Plus aria-hidden size={14} stroke={1.75} />
            {t.machines.add}
          </button>
        )}
      </div>
      {machines.length === 0 && !adding && <p className="text-xs text-neutral-500">{t.machines.othersEmpty}</p>}
      <ul className="space-y-2">
        {machines.map(machine => (
          <MachineItem key={machine.id} machine={machine} />
        ))}
      </ul>
      {adding && (
        <AddMachine
          onDone={() => {
            setAdding(false)
          }}
        />
      )}
    </Card>
  )
}

function MachineItem({ machine }: { machine: MachineRow }) {
  const [said, setSaid] = useState<null | { kind: 'failed' | 'ok' | 'testing'; text: string }>(null)
  const [renaming, setRenaming] = useState<null | string>(null)
  const [removing, setRemoving] = useState(false)
  const where =
    machine.transport === 'tailscale'
      ? `${t.machines.viaTailscale} · ${machine.host ?? ''}`
      : `${t.machines.viaSsh} · ${machine.sshHost ?? ''}`

  return (
    <li
      className="space-y-1 rounded-md bg-neutral-50 px-2 py-1.5 dark:bg-neutral-800/50"
      data-machine={machine.name}
      data-testid="machine-row"
    >
      <div className="flex items-center gap-2">
        <Machine aria-hidden className="shrink-0 text-neutral-400" size={16} stroke={1.75} />
        {renaming === null ? (
          <span className="min-w-0 flex-1 truncate text-sm">
            {machine.name} <span className="text-neutral-500">· {machine.home}</span>
          </span>
        ) : (
          <input
            aria-label={t.machines.name}
            className={`${INPUT} flex-1`}
            data-testid="machine-rename-input"
            onChange={event => {
              setRenaming(event.target.value)
            }}
            value={renaming}
          />
        )}
        {renaming === null ? (
          <>
            <button
              className={SMALL}
              data-testid="machine-test"
              disabled={said?.kind === 'testing'}
              onClick={() => {
                setSaid({ kind: 'testing', text: t.machines.testing })
                void testMachine(machine.id).then(problem => {
                  setSaid(problem ? { kind: 'failed', text: problem } : { kind: 'ok', text: t.machines.reachable })
                })
              }}
              type="button"
            >
              {t.machines.test}
            </button>
            <button
              className={SMALL}
              data-testid="machine-rename"
              onClick={() => {
                setRenaming(machine.name)
              }}
              type="button"
            >
              {t.machines.rename}
            </button>
            <button
              className={SMALL}
              data-testid="machine-remove"
              onClick={() => {
                setRemoving(true)
              }}
              type="button"
            >
              {t.machines.remove}
            </button>
          </>
        ) : (
          <>
            <button
              className={SMALL}
              data-testid="machine-rename-save"
              onClick={() => {
                void renameMachine(machine.id, renaming).then(problem => {
                  if (problem) {
                    setSaid({ kind: 'failed', text: problem })
                  } else {
                    setRenaming(null)
                  }
                })
              }}
              type="button"
            >
              {t.machines.save}
            </button>
            <button
              className={SMALL}
              onClick={() => {
                setRenaming(null)
              }}
              type="button"
            >
              {t.machines.cancel}
            </button>
          </>
        )}
      </div>
      <p className="pl-6 text-xs text-neutral-500">{where}</p>
      {removing && (
        <p className="flex flex-wrap items-center gap-2 pl-6 text-xs">
          {t.machines.removeConfirm(machine.name)}
          <button
            className={SMALL}
            data-testid="machine-remove-confirm"
            onClick={() => void removeMachine(machine.id)}
            type="button"
          >
            {t.machines.remove}
          </button>
          <button
            className={SMALL}
            onClick={() => {
              setRemoving(false)
            }}
            type="button"
          >
            {t.machines.cancel}
          </button>
        </p>
      )}
      {said && (
        <p
          className={`pl-6 text-xs [overflow-wrap:anywhere] ${said.kind === 'failed' ? 'text-red-600' : 'text-neutral-500'}`}
          data-kind={said.kind}
          data-testid="machine-said"
          role={said.kind === 'failed' ? 'alert' : 'status'}
        >
          {said.text}
        </p>
      )}
    </li>
  )
}

/** A machine from its connection code: a name, and how it is reached. */
function AddMachine({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [transport, setTransport] = useState<Transport>('tailscale')
  const [sshHost, setSshHost] = useState('')
  const [problem, setProblem] = useState('')
  const [adding, setAdding] = useState(false)

  return (
    <form
      className="space-y-2 border-t border-neutral-200 pt-3 dark:border-neutral-800"
      data-testid="machine-add-form"
      onSubmit={event => {
        event.preventDefault()
        setAdding(true)
        void addMachine({ code, name, sshHost, transport })
          .then(said => {
            if (said) {
              setProblem(said)
            } else {
              onDone()
            }
          })
          .finally(() => {
            setAdding(false)
          })
      }}
    >
      <p className="text-sm font-medium">{t.machines.addTitle}</p>
      <label className="block space-y-1 text-xs">
        <span>{t.machines.code}</span>
        {/* Hidden as a password: it holds one. */}
        <input
          autoComplete="off"
          className={`${INPUT} w-full`}
          data-testid="machine-code"
          onChange={event => {
            setCode(event.target.value)
          }}
          type="password"
          value={code}
        />
        <span className="block text-neutral-500">{t.machines.codeHint}</span>
      </label>
      <label className="block space-y-1 text-xs">
        <span>{t.machines.name}</span>
        <input
          className={`${INPUT} w-full`}
          data-testid="machine-name"
          onChange={event => {
            setName(event.target.value)
          }}
          placeholder={t.machines.namePlaceholder}
          value={name}
        />
      </label>
      <fieldset className="space-y-1 text-xs">
        <legend>{t.machines.transport}</legend>
        <div className="flex gap-4">
          {(['tailscale', 'ssh'] as const).map(each => (
            <label className="flex items-center gap-1.5" key={each}>
              <input
                checked={transport === each}
                data-testid={`machine-transport-${each}`}
                name="transport"
                onChange={() => {
                  setTransport(each)
                }}
                type="radio"
              />
              {each === 'tailscale' ? t.machines.viaTailscale : t.machines.viaSsh}
            </label>
          ))}
        </div>
      </fieldset>
      {transport === 'ssh' && (
        <label className="block space-y-1 text-xs">
          <span>{t.machines.sshHost}</span>
          <input
            className={`${INPUT} w-full`}
            data-testid="machine-ssh-host"
            onChange={event => {
              setSshHost(event.target.value)
            }}
            placeholder={t.machines.sshHostPlaceholder}
            value={sshHost}
          />
        </label>
      )}
      {problem && (
        <p className="text-xs text-red-600 [overflow-wrap:anywhere]" data-testid="machine-add-problem" role="alert">
          {problem}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <button className={BUTTON} onClick={onDone} type="button">
          {t.machines.cancel}
        </button>
        <button className={BUTTON} data-testid="machine-add-save" disabled={adding || !code || !name} type="submit">
          {adding ? t.machines.adding : t.machines.add}
        </button>
      </div>
    </form>
  )
}

function Notice() {
  const notice = useStore($machinesNotice)

  return notice ? (
    <p
      className={`rounded-md px-2 py-1.5 text-xs [overflow-wrap:anywhere] ${
        notice.kind === 'failed'
          ? 'bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200'
          : 'bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-200'
      }`}
      data-kind={notice.kind}
      data-testid="machines-notice"
      role={notice.kind === 'failed' ? 'alert' : 'status'}
    >
      {notice.text}
    </p>
  ) : null
}

const BUTTON =
  'flex items-center gap-1.5 rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800'
const SMALL =
  'shrink-0 rounded px-1.5 py-0.5 text-xs text-neutral-600 hover:bg-neutral-200 disabled:opacity-40 dark:text-neutral-300 dark:hover:bg-neutral-700'
const INPUT =
  'rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700'
