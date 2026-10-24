import path from 'node:path'

import { describe, expect, it } from 'vitest'

import { readyPort, remoteEndpoint, repositoryCommand } from './server'

describe('the ready line', () => {
  it('gives the port once the whole line arrived', () => {
    expect(readyPort('')).toBeNull()
    expect(readyPort('ATLAS_SERVER_READY port=512')).toBeNull()
    expect(readyPort('starting\nATLAS_SERVER_READY port=51234\n')).toBe(51234)
    expect(readyPort('ATLAS_SERVER_READY port=51234\r\n')).toBe(51234)
  })
})

describe('the server the app starts', () => {
  it("is the repository's atlas serve on port 0, with the app's PID", () => {
    const command = repositoryCommand(path.join('/repo', 'apps', 'desktop'), 4242)

    expect(command.command).toBe('uv')
    expect(command.cwd).toBe(path.resolve('/repo'))
    expect(command.args).toEqual([
      'run',
      '--project',
      path.resolve('/repo'),
      'atlas',
      'serve',
      '--host',
      '127.0.0.1',
      '--port',
      '0',
      '--parent-pid',
      '4242'
    ])
    expect(command.path).toEqual([])
  })

  it("runs the installation's uv, its toolchain's folders leading the server's PATH", () => {
    const tools = path.resolve('/install', 'tools')
    const programs = {
      gh: path.join(tools, 'gh', 'bin', 'gh'),
      node: path.join(tools, 'node', 'node'),
      npm: path.join(tools, 'node', 'npm'),
      uv: path.join(tools, 'uv', 'uv')
    }
    const command = repositoryCommand(path.join('/install', 'atlas', 'apps', 'desktop'), 1, null, programs)

    expect(command.command).toBe(programs.uv)
    expect(command.path).toEqual([path.join(tools, 'gh', 'bin'), path.join(tools, 'node'), path.join(tools, 'uv')])
  })
})

describe('a server already running', () => {
  it('is named by both variables, or neither', () => {
    expect(remoteEndpoint({})).toBeNull()
    expect(remoteEndpoint({ ATLAS_DESKTOP_TOKEN: 't', ATLAS_DESKTOP_URL: 'ws://box:18790/ws' })).toEqual({
      token: 't',
      url: 'ws://box:18790/ws'
    })
    expect(() => remoteEndpoint({ ATLAS_DESKTOP_URL: 'ws://box:18790/ws' })).toThrow('both')
    expect(() => remoteEndpoint({ ATLAS_DESKTOP_TOKEN: 't', ATLAS_DESKTOP_URL: 'http://box/ws' })).toThrow('ws://')
  })
})
