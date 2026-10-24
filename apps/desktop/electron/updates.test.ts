import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import {
  handOff,
  handOffCommand,
  installationPython,
  markerPid,
  parseStatus,
  reopenFlags,
  takeResult,
  updateFiles,
  type UpdateRun,
  updateRunning
} from './updates'

let folder = ''

afterEach(() => {
  if (folder) {
    rmSync(folder, { force: true, maxRetries: 10, recursive: true, retryDelay: 200 })
    folder = ''
  }
})

function scratch(): string {
  folder = mkdtempSync(path.join(tmpdir(), 'atlas-update-'))

  return folder
}

/** Node standing in for the installation's Python, running `script` as its module. */
function standIn(script: string): UpdateRun {
  return { cwd: folder, env: process.env, module: [script], python: process.execPath }
}

const RUN: UpdateRun = { cwd: '/i', env: {}, module: ['-m', 'atlas.installation'], python: '/i/.venv/bin/python' }

describe('the look for an update', () => {
  it("is read from Python's answer, and an answer it cannot read offers nothing", () => {
    const said = JSON.stringify({
      available: true,
      behind: 2,
      commits: ['One', 'Two'],
      current: 'a',
      dirty: false,
      error: '',
      reason: '',
      supported: true,
      target: 'b'
    })

    expect(parseStatus(said)).toMatchObject({ available: true, behind: 2, commits: ['One', 'Two'], commits_pt_br: [] })
    expect(parseStatus('Traceback (most recent call last)')).toMatchObject({ available: false, supported: true })
    expect(parseStatus('null').error).toContain('nothing readable')
  })

  it("runs the installation's own Python", () => {
    expect(installationPython('/i', 'linux')).toBe(path.join('/i', '.venv', 'bin', 'python'))
    expect(installationPython('C:\\i', 'win32')).toBe(path.join('C:\\i', '.venv', 'Scripts', 'python.exe'))
  })
})

describe('the hand-off', () => {
  const files = updateFiles(path.join('/data'))

  it("starts on Windows through cmd's start, hidden and not detached", () => {
    const windows = handOffCommand(RUN, files, 42, 'win32')

    expect(windows.command).toBe('cmd.exe')
    expect(windows.detached).toBe(false)
    expect(windows.args.slice(0, 7)).toEqual(['/d', '/s', '/c', 'start', '', '/b', RUN.python])
    expect(windows.args).toContain('--app-pid')
    expect(windows.args[windows.args.indexOf('--app-pid') + 1]).toBe('42')

    const posix = handOffCommand(RUN, files, 42, 'linux')
    expect((({ command, detached }) => ({ command, detached }))(posix)).toEqual({ command: RUN.python, detached: true })
    expect(posix.args.slice(0, 3)).toEqual(['-m', 'atlas.installation', 'handoff'])
  })

  it('refuses on Windows a path cmd would read as something else', () => {
    const odd = updateFiles(path.join('C:\\Users\\a%PATH%b', 'Atlas'))

    expect(() => handOffCommand(RUN, odd, 1, 'win32')).toThrow('holds % or a quote')
  })

  it("gives the app's own flags again, never Chromium's, a debugger's or the folder", () => {
    const given = [
      'C:\\atlas\\apps\\desktop',
      '--inspect=0',
      '--remote-debugging-port=0',
      '--user-data-dir=C:\\proof\\profile',
      '--lang=pt-BR',
      '--atlas-e2e',
      '--enable-features=X'
    ]

    expect(reopenFlags(given)).toEqual(['--user-data-dir=C:\\proof\\profile', '--lang=pt-BR'])
    expect(handOffCommand(RUN, files, 42, 'linux', given).args.slice(-2)).toEqual([
      '--reopen-flag=--user-data-dir=C:\\proof\\profile',
      '--reopen-flag=--lang=pt-BR'
    ])
  })

  it('took when its marker names it; one that never does leaves the app open with why', async () => {
    const at = scratch()
    const taking = path.join(at, 'takes.mjs')
    // As the Python hand-off does first: the marker, with its own process id and when it began.
    writeFileSync(
      taking,
      [
        "import { writeFileSync } from 'node:fs'",
        "const marker = process.argv[process.argv.indexOf('--marker') + 1]",
        'writeFileSync(marker, JSON.stringify({ pid: process.pid, started_at: new Date().toISOString() }))',
        'setTimeout(() => undefined, 3000)'
      ].join('\n')
    )
    const silent = path.join(at, 'silent.mjs')
    writeFileSync(silent, 'setTimeout(() => undefined, 3000)\n')
    const own = updateFiles(at)

    await handOff(standIn(taking), own, process.pid)
    expect(updateRunning(own)).toBe(true)
    await expect(handOff(standIn(silent), own, process.pid, { waitMs: 1_500 })).rejects.toThrow(
      'did not start in 1.5 s'
    )
    const missing = { ...standIn(silent), python: path.join(at, 'no-python') }
    await expect(handOff(missing, own, process.pid)).rejects.toThrow("The installation's Python is missing")
  })
})

describe('what the update says', () => {
  it('is running only while its marker is fresh and its process alive', () => {
    const now = Date.parse('2026-10-18T12:00:00.000Z')
    const marker = (pid: number, at: string) => JSON.stringify({ pid, started_at: at })

    expect(markerPid(null, now)).toBeNull()
    expect(markerPid('{', now)).toBeNull()
    expect(markerPid(marker(7, '2026-10-18T11:59:00.000Z'), now)).toBe(7)
    expect(markerPid(marker(7, '2026-10-18T11:00:00.000Z'), now)).toBeNull()

    const own = updateFiles(scratch())
    writeFileSync(own.marker, marker(process.pid, new Date().toISOString()))
    expect(updateRunning(own)).toBe(true)
    writeFileSync(own.marker, marker(2 ** 22 + 12_345, new Date().toISOString()))
    expect(updateRunning(own)).toBe(false)
  })

  it('ended is read once, as the app opens', () => {
    const own = updateFiles(scratch())
    const ended = {
      after: 'b',
      before: 'a',
      commits: ['b1c2d3e Step 18'],
      ended_at: '2026-10-18T12:00:00.000Z',
      error: '',
      log: own.log,
      ok: true,
      reopen: ''
    }
    writeFileSync(own.result, JSON.stringify(ended))

    expect(takeResult(own)).toEqual(ended)
    expect(takeResult(own)).toBeNull()
    expect(() => readFileSync(own.result)).toThrow()
  })
})
