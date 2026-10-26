import { describe, expect, it } from 'vitest'

import { closesTab, type KeyInput } from './keys'

const key = (fields: Partial<KeyInput>): KeyInput => ({
  alt: false,
  control: false,
  key: 'w',
  meta: true,
  shift: false,
  type: 'keyDown',
  ...fields
})

describe('the keys the main process takes', () => {
  it('closes the tab on ⌘W on macOS, not the window', () => {
    expect(closesTab(key({}), 'darwin')).toBe(true)
    expect(closesTab(key({ key: 'W' }), 'darwin')).toBe(true)
  })

  it('leaves every other key, and every other system, to the window', () => {
    expect(closesTab(key({}), 'win32')).toBe(false)
    expect(closesTab(key({}), 'linux')).toBe(false)
    expect(closesTab(key({ shift: true }), 'darwin')).toBe(false)
    expect(closesTab(key({ alt: true }), 'darwin')).toBe(false)
    expect(closesTab(key({ control: true }), 'darwin')).toBe(false)
    expect(closesTab(key({ meta: false }), 'darwin')).toBe(false)
    expect(closesTab(key({ type: 'keyUp' }), 'darwin')).toBe(false)
    expect(closesTab(key({ key: 'q' }), 'darwin')).toBe(false)
  })
})
