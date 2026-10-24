import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { activeHome, AttachedServer, type Cli, failure, homeFolder, readAlwaysOn } from './always-on'

const made: string[] = []

afterEach(() => {
  for (const folder of made.splice(0)) {
    rmSync(folder, { force: true, recursive: true })
  }
})

function root(): string {
  const created = mkdtempSync(path.join(tmpdir(), 'atlas-always-on-'))
  made.push(created)

  return created
}

describe("an always-on home's files", () => {
  it('are where atlas.home keeps them', () => {
    expect(homeFolder('/r', 'default')).toBe('/r')
    expect(homeFolder('/r', 'work')).toBe(path.join('/r', 'homes', 'work'))

    const at = root()
    expect(activeHome(at)).toBe('default')
    writeFileSync(path.join(at, 'active_home'), 'work\n')
    expect(activeHome(at)).toBe('work')
  })

  it('say its port and whether it is open, or nothing', () => {
    const at = root()
    expect(readAlwaysOn(at)).toBeNull()

    writeFileSync(path.join(at, 'always_on.json'), '{"port": 18800, "open": true}')
    expect(readAlwaysOn(at)).toEqual({ open: true, port: 18800 })
    writeFileSync(path.join(at, 'always_on.json'), '{"port": 18800}')
    expect(readAlwaysOn(at)).toEqual({ open: false, port: 18800 })

    for (const text of ['not json', '{"port": "18800"}', '[]']) {
      writeFileSync(path.join(at, 'always_on.json'), text)
      expect(readAlwaysOn(at)).toBeNull()
    }
  })
})

describe('why atlas failed', () => {
  it("is its own error line, without uv's warnings", () => {
    expect(
      failure({ stderr: 'warning: VIRTUAL_ENV is ignored\natlas: The home is not always on.\n', stdout: '' })
    ).toBe('The home is not always on.')
    expect(failure({ stderr: 'Traceback\nValueError: boom\n', stdout: '' })).toBe('ValueError: boom')
  })
})

describe('the server of an always-on home', () => {
  it('says so when the home is no longer always on', async () => {
    const at = root()
    const cli: Cli = () => Promise.reject(new Error('not run'))

    await expect(new AttachedServer('default', at, cli, () => undefined).endpoint()).rejects.toThrow(
      /no longer always on/
    )
  })

  it('starts the service when its server does not answer, and says why it could not', async () => {
    const at = path.join(root(), 'homes', 'work')
    mkdirSync(at, { recursive: true })
    // A port nothing listens on: the probe fails at once.
    writeFileSync(path.join(at, 'always_on.json'), '{"port": 1, "open": false}')
    const runs: string[][] = []
    const cli: Cli = (_home, args) => {
      runs.push(args)

      return Promise.resolve(
        args[0] === 'server'
          ? { code: 0, stderr: '', stdout: 'the-token\n' }
          : { code: 1, stderr: 'atlas: The service did not start.\n', stdout: '' }
      )
    }
    const said: string[] = []

    await expect(new AttachedServer('work', at, cli, line => said.push(line)).endpoint()).rejects.toThrow(
      'The service did not start.'
    )
    expect(runs).toEqual([
      ['server', 'token'],
      ['always-on', 'start']
    ])
    expect(said.join('\n')).toMatch(/does not answer; starting its service/)
  })
})
