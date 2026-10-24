import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { parseToolchain, readToolchain, withPath } from './toolchain'

const made: string[] = []

/** An installation as the installer leaves it: the checkout, and `tools/` beside it. */
function installation(file: null | string, programs: string[] = []): string {
  const root = mkdtempSync(path.join(tmpdir(), 'atlas-toolchain-'))
  made.push(root)
  const tools = path.join(root, 'tools')
  mkdirSync(path.join(root, 'atlas'), { recursive: true })

  for (const program of programs) {
    mkdirSync(path.dirname(path.join(tools, program)), { recursive: true })
    writeFileSync(path.join(tools, program), '')
  }

  if (file !== null) {
    mkdirSync(tools, { recursive: true })
    writeFileSync(path.join(tools, 'toolchain.json'), file)
  }

  return path.join(root, 'atlas')
}

afterEach(() => {
  for (const root of made.splice(0)) {
    rmSync(root, { force: true, recursive: true })
  }
})

describe('the toolchain file', () => {
  it('names programs under the tools folder', () => {
    expect(parseToolchain('{"version": 1, "programs": {"uv": "uv-0.12.5/uv.exe", "gh": "gh/bin/gh"}}')).toEqual({
      gh: 'gh/bin/gh',
      uv: 'uv-0.12.5/uv.exe'
    })
  })

  it.each([
    ['{', 'damaged'],
    ['[]', 'not an object'],
    ['{"version": 2, "programs": {}}', 'version 2'],
    ['{"version": 1}', 'no programs'],
    ['{"version": 1, "programs": {"UV": "uv"}}', 'a program named "UV"'],
    ['{"version": 1, "programs": {"uv": ""}}', "uv's path"],
    ['{"version": 1, "programs": {"uv": "uv\\\\uv.exe"}}', "uv's path"],
    ['{"version": 1, "programs": {"uv": "/usr/bin/uv"}}', 'leaves the tools folder'],
    ['{"version": 1, "programs": {"uv": "../uv"}}', 'leaves the tools folder'],
    ['{"version": 1, "programs": {"uv": "C:/uv.exe"}}', 'leaves the tools folder']
  ])('refuses %s and says to run the installer', (text, said) => {
    expect(() => parseToolchain(text)).toThrow('run the installer again')
    expect(() => parseToolchain(text)).toThrow(said)
  })
})

describe("the installation's programs", () => {
  it('are none in a development checkout', () => {
    expect(readToolchain(installation(null))).toEqual({})
  })

  it('are full paths beside the checkout', () => {
    const root = installation('{"version": 1, "programs": {"uv": "uv-0.12.5/uv"}}', ['uv-0.12.5/uv'])

    expect(readToolchain(root)).toEqual({ uv: path.join(path.dirname(root), 'tools', 'uv-0.12.5', 'uv') })
  })

  it('are refused when one the file names is missing', () => {
    const root = installation('{"version": 1, "programs": {"uv": "uv-0.12.5/uv"}}')

    expect(() => readToolchain(root)).toThrow('uv is missing')
  })
})

describe("the server's PATH", () => {
  it('has the folders first, and the one variable Windows names Path replaced', () => {
    const env = withPath({ Other: 'x', Path: 'C:\\Windows' }, ['C:\\tools\\uv'])

    expect(Object.keys(env).filter(name => name.toUpperCase() === 'PATH')).toEqual(['Path'])
    expect(env.Path).toBe(['C:\\tools\\uv', 'C:\\Windows'].join(path.delimiter))
    expect(env.Other).toBe('x')
  })

  it('is left alone without a toolchain', () => {
    const env = { PATH: '/usr/bin' }

    expect(withPath(env, [])).toBe(env)
  })
})
