/**
 * The SSH tunnel for real (step 15): the system's `ssh` against a real sshd in a container with Atlas
 * on its loopback, run by tests/docker/ssh-tunnel.sh, which says where it is. Skipped otherwise.
 * Nothing stands in for `ssh`, sshd or Atlas: the run's key and known hosts come in a config file
 * (`-F`), as the user's ~/.ssh/config would give them.
 */
import net from 'node:net'

import { describe, expect, it } from 'vitest'

import { RemoteMachine } from './remote-machine'

const target = process.env.ATLAS_TEST_SSHD ?? ''
const configFile = process.env.ATLAS_TEST_SSH_CONFIG ?? ''
const token = process.env.ATLAS_TEST_SSHD_TOKEN ?? ''

function refused(port: number): Promise<boolean> {
  return new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port }, () => {
      socket.destroy()
      resolve(false)
    })
    socket.once('error', () => {
      resolve(true)
    })
  })
}

describe.runIf(target)('the SSH tunnel against a real sshd', () => {
  it('reaches the Atlas there with its token, and a site up there for the preview', async () => {
    const said: string[] = []
    const lab = new RemoteMachine(
      { home: 'default', host: null, id: 'lab', name: 'Lab', port: 18790, sshHost: target, transport: 'ssh' },
      () => token,
      line => said.push(line),
      { configFile }
    )

    try {
      // Both legs probed through the tunnel: /health, and the WebSocket with the token.
      const endpoint = await lab.endpoint()
      expect(endpoint.url).toMatch(/^ws:\/\/127\.0\.0\.1:\d+\/ws$/)
      expect(said.join('\n')).toContain('Opening an SSH tunnel')

      const site = await lab.reach('http://localhost:3000/hello.txt')
      expect('url' in site).toBe(true)
      const url = 'url' in site ? site.url : ''
      expect(url).toMatch(/^http:\/\/127\.0\.0\.1:\d+\/hello\.txt$/)
      expect(await (await fetch(url)).text()).toBe('a site up on the other machine\n')

      lab.close()
      const port = Number(new URL(url).port)
      await expect.poll(() => refused(port), { timeout: 10_000 }).toBe(true)
    } finally {
      lab.close()
    }
  }, 120_000)
})
