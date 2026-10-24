/**
 * The settings screen on screen (step 15b, docs/ARCHITECTURE.md, The settings screen): open or not,
 * and the section on its right. It opens on a section and keeps the others a click away on its left.
 */
import { atom } from 'nanostores'

import { detectExecutors, refreshPlugins } from './app'
import { $machinesNotice, refreshMachines, refreshThisMachine } from './machines'

export const SECTIONS = [
  'general',
  'model',
  'personalization',
  'safety',
  'workspace',
  'plugins',
  'executors',
  'machines'
] as const
export type SettingsSection = (typeof SECTIONS)[number]

export const $settings = atom(false)
export const $settingsSection = atom<SettingsSection>('general')

export function openSettings(section: SettingsSection = 'general'): void {
  $settings.set(true)
  showSection(section)
}

export function showSection(section: SettingsSection): void {
  $settingsSection.set(section)
  if (section === 'machines') {
    $machinesNotice.set(null)
    void refreshMachines()
    void refreshThisMachine()
  } else if (section === 'plugins') {
    refreshPlugins()
  } else if (section === 'executors') {
    void detectExecutors()
  }
}

export function closeSettings(): void {
  $settings.set(false)
}
