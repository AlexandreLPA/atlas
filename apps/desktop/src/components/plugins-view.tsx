import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import { $screenErrors } from '@/plugins/registry'
import type { PairingRow, PluginRow, PluginSecretRow, PluginSettingRow } from '@/protocol/protocol.generated'
import {
  $pairings,
  $pluginError,
  $plugins,
  approvePairing,
  disablePlugin,
  enablePlugin,
  forgetPairing,
  reloadPlugins,
  setPluginSetting,
  storePluginSecret
} from '@/store/app'

/**
 * The plugins (docs/ARCHITECTURE.md, Plugins): each one off until turned on, and turning one on
 * shows what it declares first, for consent; its secrets and settings; and the
 * people its channels let in, or who wait with a code (a pairing, approved here instead of
 * only in a terminal). The settings screen's Plugins since step 15b.
 */
export function PluginsSection() {
  const plugins = useStore($plugins)
  const pairings = useStore($pairings)
  const error = useStore($pluginError)
  const channels = plugins.some(plugin => plugin.channels.length > 0)

  return (
    <div data-testid="plugins-view">
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <p className="text-sm text-neutral-500">{t.plugins.intro}</p>
          {/* Step 16n: the plugins loaded again; the server also loads a saved file again. */}
          <button
            className={`${BUTTON} shrink-0 text-sm`}
            data-testid="plugins-reload"
            onClick={() => void reloadPlugins()}
            title={t.plugins.reloadHint}
            type="button"
          >
            {t.plugins.reload}
          </button>
        </div>
        {error && (
          <p
            className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
            role="alert"
          >
            {error}
          </p>
        )}
        {plugins.length === 0 && <p className="text-neutral-500">{t.plugins.empty}</p>}
        {plugins.map(plugin => (
          <Plugin key={`${plugin.name}-${String(plugin.bundled)}`} plugin={plugin} />
        ))}
        {channels && <People pairings={pairings} />}
      </div>
    </div>
  )
}

function Plugin({ plugin }: { plugin: PluginRow }) {
  const [asking, setAsking] = useState(false)
  const on = plugin.enabled && plugin.consented
  const state = plugin.running ? t.plugins.running : on ? t.plugins.on : t.plugins.off
  // Its screen loads in the app (step 16m), so why it did not is the app's to say.
  const screenError = useStore($screenErrors)[plugin.name]

  return (
    <div
      className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800"
      data-plugin={plugin.name}
      data-testid="plugin-row"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-medium">
            {plugin.name}
            {plugin.version && <span className="ml-2 text-sm font-normal text-neutral-500">{plugin.version}</span>}
            <span
              className={`ml-2 rounded px-1.5 py-0.5 text-xs font-normal ${
                plugin.running
                  ? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300'
                  : 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300'
              }`}
              data-testid="plugin-state"
            >
              {state}
            </span>
          </p>
          <p className="text-sm text-neutral-500">
            {plugin.description}
            {plugin.bundled ? ` · ${t.plugins.shipped}` : ''}
          </p>
        </div>
        <div className="shrink-0 text-sm">
          {on ? (
            <button
              className={BUTTON}
              data-testid="plugin-disable"
              onClick={() => void disablePlugin(plugin.name)}
              type="button"
            >
              {t.plugins.disable}
            </button>
          ) : (
            !asking && (
              <button
                className={BUTTON}
                data-testid="plugin-enable"
                onClick={() => {
                  setAsking(true)
                }}
                type="button"
              >
                {t.plugins.enable}
              </button>
            )
          )}
        </div>
      </div>
      {plugin.problem && (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-400" data-testid="plugin-problem">
          {plugin.problem}
        </p>
      )}
      {on && screenError && (
        <p className="mt-2 text-sm text-amber-700 dark:text-amber-400" data-testid="plugin-screen-problem">
          {t.plugins.screenNotLoaded(screenError)}
        </p>
      )}
      {asking && !on && (
        <div className="mt-3 rounded-md bg-neutral-50 p-3 text-sm dark:bg-neutral-900" data-testid="plugin-consent">
          <p className="font-medium">{t.plugins.consentIntro}</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {plugin.declaration.map(line => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <button
              className={`${BUTTON} bg-violet-600 text-white hover:bg-violet-700 dark:hover:bg-violet-700`}
              data-testid="plugin-consent-yes"
              onClick={() => {
                setAsking(false)
                void enablePlugin(plugin)
              }}
              type="button"
            >
              {t.plugins.enableNow}
            </button>
            <button
              className={BUTTON}
              onClick={() => {
                setAsking(false)
              }}
              type="button"
            >
              {t.plugins.cancel}
            </button>
          </div>
        </div>
      )}
      {plugin.secrets.map(secret => (
        <Secret key={secret.name} plugin={plugin.name} secret={secret} />
      ))}
      {plugin.settings.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-neutral-500">{t.plugins.settings}</summary>
          {plugin.settings.map(setting => (
            <Setting key={setting.name} plugin={plugin.name} setting={setting} />
          ))}
        </details>
      )}
    </div>
  )
}

/** A secret the plugin reads: a field to paste it in while the vault has none; once stored, only
 *  that it is, and a button to change it. A key stored for something else, such as the OpenAI
 *  provider's, counts: the field would read as if it were asked for again. */
function Secret({ plugin, secret }: { plugin: string; secret: PluginSecretRow }) {
  const [value, setValue] = useState('')
  const [changing, setChanging] = useState(false)

  return (
    <div className="mt-3 text-sm" data-stored={secret.stored} data-testid="plugin-secret">
      <p>
        <span className="font-mono">{secret.name}</span>
        <span className="ml-2 text-neutral-500">{secret.stored ? t.plugins.stored : t.plugins.notStored}</span>
        {secret.stored && !changing && (
          <button
            className="ml-2 text-violet-600 hover:underline"
            data-testid="plugin-secret-change"
            onClick={() => {
              setChanging(true)
            }}
            type="button"
          >
            {t.plugins.changeSecret}
          </button>
        )}
      </p>
      {secret.description && (
        <p className="text-neutral-500">
          {secret.description}{' '}
          {secret.url && (
            <a className="text-violet-600 hover:underline" href={secret.url} rel="noreferrer" target="_blank">
              {secret.url}
            </a>
          )}
        </p>
      )}
      {(!secret.stored || changing) && (
        <form
          className="mt-1 flex gap-2"
          onSubmit={event => {
            event.preventDefault()
            void storePluginSecret(plugin, secret.name, value).then(done => {
              if (done) {
                setValue('')
                setChanging(false)
              }
            })
          }}
        >
          <input
            aria-label={secret.name}
            autoComplete="off"
            className={INPUT}
            data-testid="plugin-secret-input"
            onChange={event => {
              setValue(event.target.value)
            }}
            placeholder={t.plugins.secretPlaceholder}
            type="password"
            value={value}
          />
          <button className={BUTTON} data-testid="plugin-secret-save" disabled={!value.trim()} type="submit">
            {t.plugins.save}
          </button>
        </form>
      )}
    </div>
  )
}

function Setting({ plugin, setting }: { plugin: string; setting: PluginSettingRow }) {
  const [value, setValue] = useState(setting.value)

  return (
    <form
      className="mt-2"
      onSubmit={event => {
        event.preventDefault()
        void setPluginSetting(plugin, setting.name, value)
      }}
    >
      <label className="block font-mono" htmlFor={`${plugin}-${setting.name}`}>
        {setting.name}
      </label>
      {setting.description && <p className="text-neutral-500">{setting.description}</p>}
      <div className="mt-1 flex gap-2">
        <input
          className={INPUT}
          id={`${plugin}-${setting.name}`}
          onChange={event => {
            setValue(event.target.value)
          }}
          value={value}
        />
        <button className={BUTTON} type="submit">
          {t.plugins.save}
        </button>
        <button
          className={BUTTON}
          onClick={() => {
            setValue(setting.default)
            void setPluginSetting(plugin, setting.name, null)
          }}
          title={t.plugins.defaultValue(setting.default)}
          type="button"
        >
          {t.plugins.reset}
        </button>
      </div>
    </form>
  )
}

function People({ pairings }: { pairings: PairingRow[] }) {
  return (
    <section className="space-y-2" data-testid="pairings">
      <h2 className="font-semibold">{t.plugins.people}</h2>
      <p className="text-sm text-neutral-500">{t.plugins.peopleIntro}</p>
      {pairings.length === 0 && <p className="text-sm text-neutral-500">{t.plugins.noPeople}</p>}
      <ul className="space-y-1 text-sm">
        {pairings.map(pairing => (
          <li
            className="flex items-center justify-between gap-4"
            data-testid="pairing-row"
            key={`${pairing.channel}-${pairing.user}`}
          >
            <span className="min-w-0 truncate">
              {pairing.name} <span className="text-neutral-500">· {pairing.channel} · </span>
              {pairing.approved ? (
                <span className="text-neutral-500">{t.plugins.approved}</span>
              ) : (
                <span className="font-mono">{t.plugins.waits(pairing.code)}</span>
              )}
            </span>
            <span className="flex shrink-0 gap-2">
              {!pairing.approved && (
                <button
                  className={BUTTON}
                  data-testid="pairing-approve"
                  onClick={() => void approvePairing(pairing.code)}
                  type="button"
                >
                  {t.plugins.approve}
                </button>
              )}
              <button
                className={BUTTON}
                onClick={() => void forgetPairing(pairing.channel, pairing.user)}
                type="button"
              >
                {t.plugins.remove}
              </button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

const BUTTON =
  'rounded-md border border-neutral-300 px-2 py-1 hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800'
const INPUT =
  'min-w-0 flex-1 rounded-md border border-neutral-300 bg-white px-2 py-1 dark:border-neutral-700 dark:bg-neutral-900'
