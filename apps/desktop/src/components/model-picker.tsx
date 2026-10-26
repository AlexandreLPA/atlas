import { useStore } from '@nanostores/react'

import { t } from '@/i18n'
import type { ConversationModel, ModelOption, ProviderOption } from '@/protocol/protocol.generated'
import { $modelOptions, $models, $selected, DRAFT, setModel } from '@/store/app'

import { PillSelect } from './ui/pill'

const SEPARATOR = '\u0000'

/**
 * The conversation's model and effort: a model pill and an effort pill in the composer
 * (docs/ARCHITECTURE.md, Daily use). The providers with a key, each with its curated models; the
 * effort lists the levels the model takes and is gone for one that does not reason. A pick is for
 * this conversation only and applies from its next turn, so neither is disabled while a turn runs,
 * Prices stay hidden. Both show what runs, never "Default": the
 * effort is Atlas's default when none was picked, and the list marks the defaults (asked for by
 * the user on 17/10/2026).
 */
export function ModelPicker({ connected }: { connected: boolean }) {
  const selected = useStore($selected)
  const models = useStore($models)
  const options = useStore($modelOptions)

  if (!options) {
    return null
  }

  const current: ConversationModel = models[selected ?? DRAFT] ?? options.default

  if (!current.provider || !current.model) {
    return <span className="text-xs text-muted-foreground">{t.composer.noModel}</span>
  }

  const providers = withCurrent(options.providers, current.provider, current.model)
  const info = providers.find(p => p.id === current.provider)?.models.find(m => m.id === current.model)
  const efforts = info?.reasoning === false ? [] : (info?.efforts ?? [])
  // The server says the effort a conversation runs on, Atlas's default when it picked none.
  const effort = current.effort ?? info?.default_effort ?? null
  const isDefault = (provider: string, model: string) =>
    provider === options.default.provider && model === options.default.model

  const pickModel = (value: string) => {
    const [provider = '', model = ''] = value.split(SEPARATOR)
    const next = providers.find(p => p.id === provider)?.models.find(m => m.id === model)
    // An effort the new model does not take goes back to Atlas's default for it.
    const kept = effort && next?.efforts?.includes(effort) ? effort : null
    void setModel(provider, model, kept)
  }

  return (
    <div className="flex items-center gap-2 text-xs">
      <PillSelect
        aria-label={t.composer.model}
        data-testid="model-select"
        disabled={!connected}
        onChange={event => {
          pickModel(event.target.value)
        }}
        title={t.composer.model}
        value={`${current.provider}${SEPARATOR}${current.model}`}
      >
        {providers.map(provider => (
          <optgroup key={provider.id} label={provider.name}>
            {provider.models.map(model => (
              <option key={model.id} value={`${provider.id}${SEPARATOR}${model.id}`}>
                {isDefault(provider.id, model.id) ? t.composer.defaulted(model.name) : model.name}
              </option>
            ))}
          </optgroup>
        ))}
      </PillSelect>
      {efforts.length > 0 && (
        <PillSelect
          aria-label={t.composer.effort}
          disabled={!connected}
          onChange={event => {
            void setModel(current.provider ?? '', current.model ?? '', event.target.value || null)
          }}
          title={t.composer.effort}
          value={effort ?? ''}
        >
          {effort === null && <option value="" />}
          {efforts.map(level => (
            <option key={level} value={level}>
              {level === info?.default_effort
                ? t.composer.defaulted(t.composer.effortLevel(level))
                : t.composer.effortLevel(level)}
            </option>
          ))}
        </PillSelect>
      )}
    </div>
  )
}

/** The offer, with the conversation's model in it even when the curated list lacks it (a model
 * named in config.toml, or one the list no longer carries). */
export function withCurrent(providers: ProviderOption[], provider: string, model: string): ProviderOption[] {
  const found = providers.find(p => p.id === provider)

  if (found?.models.some(m => m.id === model)) {
    return providers
  }

  const extra: ModelOption = { default_effort: null, efforts: null, id: model, name: model, reasoning: null }

  if (found) {
    return providers.map(p => (p === found ? { ...p, models: [...p.models, extra] } : p))
  }

  return [...providers, { id: provider, models: [extra], name: provider }]
}
