import { mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { backupName, homesRoot, lastHome, rememberHome } from './homes'

const made: string[] = []

/** A machine's homes, `named` under `homes/`, and the app's own folder beside them. */
function machine(...named: string[]): { file: string; root: string } {
  const folder = mkdtempSync(path.join(tmpdir(), 'atlas-last-home-'))
  made.push(folder)
  const root = path.join(folder, '.atlas')

  for (const name of named) {
    mkdirSync(path.join(root, 'homes', name), { recursive: true })
  }

  return { file: path.join(folder, 'app', 'last-home.json'), root }
}

afterEach(() => {
  for (const folder of made.splice(0)) {
    rmSync(folder, { force: true, recursive: true })
  }
})

describe('the last home', () => {
  it('opens the home the app showed last', () => {
    const { file, root } = machine('work')
    expect(lastHome(file, root)).toBeNull()

    rememberHome(file, 'work')
    expect(lastHome(file, root)).toBe('work')
    rememberHome(file, 'default')
    expect(lastHome(file, root)).toBe('default')
    expect(readdirSync(path.dirname(file))).toEqual(['last-home.json'])
  })

  it('gives way to the active home once the last one is gone', () => {
    const { file, root } = machine('work')
    rememberHome(file, 'work')
    rmSync(path.join(root, 'homes', 'work'), { recursive: true })

    expect(lastHome(file, root)).toBeNull()
  })

  it('reads nothing it could not have written', () => {
    const { file, root } = machine('work')
    mkdirSync(path.dirname(file), { recursive: true })

    for (const text of ['not json', '{"home": "../outside"}', '{"home": 3}', '{}']) {
      writeFileSync(file, text)
      expect(lastHome(file, root)).toBeNull()
    }

    rememberHome(file, '../outside')
    expect(lastHome(file, root)).toBeNull()
  })

  it('is not used when ATLAS_HOME names one home itself', () => {
    expect(homesRoot({ ATLAS_HOME: path.join('/data', 'homes', 'work') })).toBeNull()
    expect(homesRoot({ ATLAS_HOME: path.join('/data', 'atlas') })).toBe(path.join('/data', 'atlas'))
    expect(homesRoot({}, path.join('/users', 'ana'))).toBe(path.join('/users', 'ana', '.atlas'))
  })
})

describe('the name the save dialog offers', () => {
  it('is the name atlas backup gives', () => {
    expect(backupName('default', new Date('2026-10-15T09:08:07.654Z'))).toBe(
      'atlas-backup-default-20261015T090807Z.zip'
    )
  })
})
