import path from 'node:path'

import { describe, expect, it } from 'vitest'

import { sshProgram, SshTunnel, tunnelArgs } from './ssh-tunnel'

describe("the tunnel's ssh", () => {
  it('never prompts, checks the key, stays alive, and reads the host as nothing but a host', () => {
    const args = tunnelArgs('me@shop', [{ local: 50001, remote: 18800 }])

    expect(args.slice(0, 1)).toEqual(['-N'])
    for (const option of [
      'BatchMode=yes',
      'StrictHostKeyChecking=accept-new',
      'ExitOnForwardFailure=yes',
      'ServerAliveInterval=15'
    ]) {
      expect(args[args.indexOf(option) - 1]).toBe('-o')
    }
    expect(args).toContain('127.0.0.1:50001:127.0.0.1:18800')
    expect(args.slice(-2)).toEqual(['--', 'me@shop'])
  })

  it("is the system's OpenSSH on Windows, then Git's, then the PATH's", () => {
    const env = { ProgramFiles: 'C:\\Program Files', SystemRoot: 'C:\\Windows' }
    const system = path.win32.join('C:\\Windows', 'System32', 'OpenSSH', 'ssh.exe')
    const git = path.win32.join('C:\\Program Files', 'Git', 'usr', 'bin', 'ssh.exe')

    expect(sshProgram(env, () => true, 'win32')).toBe(system)
    expect(sshProgram(env, file => file === git, 'win32')).toBe(git)
    expect(sshProgram(env, () => false, 'win32')).toBe('ssh')
    expect(sshProgram(env, () => true, 'linux')).toBe('ssh')
  })
})

describe('a tunnel that falls', () => {
  // Node stands for an ssh that ends at once: it refuses `-N` and says so on stderr.
  const failing = () =>
    new SshTunnel({ host: 'shop', log: () => undefined, program: process.execPath, remotePort: 18800 })

  it("says ssh's own last words", async () => {
    await expect(failing().port()).rejects.toThrow(/ssh to shop ended \(\d+\)\.\n.*-N/)
  })

  it('is opened again 5 times a minute at most', async () => {
    const tunnel = failing()

    for (let attempt = 1; attempt <= 5; attempt++) {
      await expect(tunnel.port()).rejects.toThrow(/ended/)
    }

    await expect(tunnel.port()).rejects.toThrow(/fell 5 times in a minute/)
  })

  it('opens nothing once closed', async () => {
    const tunnel = failing()
    tunnel.close()

    await expect(tunnel.port()).rejects.toThrow(/was closed/)
  })
})
