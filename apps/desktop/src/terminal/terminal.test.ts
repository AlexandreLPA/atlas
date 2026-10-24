/**
 * The terminal's reading of a shell (step 16f), the shell now the server's (step 15): what it does
 * with the marks a shell writes. The real shells, in the real pseudo-terminals, are proved where
 * they now run: `tests/terminals` and `tests/server/test_terminal.py` on the server, and the e2e's
 * ▶ and `!` through the real app. Here a scripted shell writes what PowerShell and cmd write.
 */
import { afterEach, describe, expect, it } from 'vitest'

import { type LateEnd, newNonce, type Pty, Terminal, TIMING } from './terminal'

const NONCE = 'f00df00df00df00df00df00df00df00d'
const mark = (body: string) => `\x1b]133;${body};nonce=${NONCE}\x07`
const PROMPT = `${mark('A')}PS C:\\work> `

/** A shell that answers each line typed as `answer` says. */
class ScriptedShell implements Pty {
  typed: string[] = []
  private readonly data: ((data: string) => void)[] = []
  private readonly exit: ((event: { exitCode: number }) => void)[] = []

  constructor(private readonly answer: (line: string, say: (text: string) => void) => void) {}

  onData(listener: (data: string) => void): void {
    this.data.push(listener)
  }

  onExit(listener: (event: { exitCode: number }) => void): void {
    this.exit.push(listener)
  }

  say(text: string): void {
    for (const listener of this.data) {
      listener(text)
    }
  }

  write(data: string): void {
    this.typed.push(data)

    if (data.endsWith('\r')) {
      this.answer(data.slice(0, -1), text => {
        this.say(text)
      })
    }
  }

  resize(): void {
    // Nothing to draw.
  }

  kill(): void {
    for (const listener of this.exit) {
      listener({ exitCode: 1 })
    }
  }
}

/** A command that prints `output` and ends with `exit`, as PowerShell marks it. */
function ends(output: string, exit = 0) {
  return (line: string, say: (text: string) => void) => {
    setTimeout(() => {
      say(`${line}\r\n${mark('C')}${output}\r\n${mark(`D;${String(exit)}`)}${PROMPT}`)
    }, 10)
  }
}

const open: Terminal[] = []

afterEach(() => {
  for (const terminal of open.splice(0)) {
    terminal.dispose()
  }
})

function start(shell: ScriptedShell, marks = true, late: (end: LateEnd) => void = () => undefined, endMs = 30_000) {
  const terminal = new Terminal(
    shell,
    { id: 't1', marks, shell: marks ? 'pwsh' : 'cmd' },
    NONCE,
    (_id, end) => {
      late(end)
    },
    { ...TIMING, endMs, quietMs: 50, startMs: 200 }
  )
  open.push(terminal)

  return terminal
}

describe("the terminal's reading of its shell", () => {
  it('runs a command clicked before the prompt, once it shows, and says its output and exit code', async () => {
    const shell = new ScriptedShell(ends('atlas-ok'))
    const terminal = start(shell)
    expect(terminal.ready).toBe(false)

    const running = terminal.run('echo atlas-ok')
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(shell.typed).toEqual([])
    shell.say(PROMPT)

    expect(await running).toEqual({ exitCode: 0, output: 'atlas-ok', status: 'completed' })
    expect(shell.typed).toEqual(['echo atlas-ok\r'])
  })

  it("says a program's exit code when it fails", async () => {
    const shell = new ScriptedShell(ends('broke', 3))
    const terminal = start(shell)
    shell.say(PROMPT)

    expect(await terminal.run('node broke.js')).toEqual({ exitCode: 3, output: 'broke', status: 'completed' })
  })

  it('gives a command that does not end its output so far, and tells its end later', async () => {
    const shell = new ScriptedShell((line, say) => {
      say(`${line}\r\n${mark('C')}serving\r\n`)
      setTimeout(() => {
        say(`stopped\r\n${mark('D;2')}${PROMPT}`)
      }, 300)
    })
    const ended: LateEnd[] = []
    const terminal = start(shell, true, end => ended.push(end), 100)
    shell.say(PROMPT)

    const result = await terminal.run('npm run dev')

    expect(result).toEqual({ exitCode: null, output: 'serving', status: 'running' })
    expect(terminal.busy).toBe(true)
    await expect.poll(() => ended.length).toBe(1)
    expect(ended[0]).toEqual({ command: 'npm run dev', exitCode: 2, output: 'serving\nstopped' })
    expect(terminal.busy).toBe(false)
  })

  it('refuses a second command while one runs: the window opens another tab', async () => {
    const shell = new ScriptedShell((line, say) => {
      say(`${line}\r\n${mark('C')}`)
      setTimeout(() => {
        say(`${mark('D;0')}${PROMPT}`)
      }, 100)
    })
    const terminal = start(shell)
    shell.say(PROMPT)
    const first = terminal.run('sleep 1')

    await expect(terminal.run('echo 1')).rejects.toThrow('busy')
    expect((await first).status).toBe('completed')
  })

  it('interrupts a command that never starts, and says so', async () => {
    const shell = new ScriptedShell(() => undefined)
    const terminal = start(shell)
    shell.say(PROMPT)

    const result = await terminal.run('a line the shell would not take')

    expect(result.status).toBe('unfinished')
    expect(shell.typed).toEqual(['a line the shell would not take\r', '\x03'])
  })

  it('ends a command in cmd, which writes no marks, when it is quiet with a prompt', async () => {
    const shell = new ScriptedShell((line, say) => {
      say(`${line}\r\natlas-cmd\r\n\r\nC:\\work>`)
    })
    const terminal = start(shell, false)
    shell.say('Microsoft Windows\r\n\r\nC:\\work>')

    expect(await terminal.run('echo atlas-cmd')).toEqual({ exitCode: null, output: 'atlas-cmd', status: 'completed' })
  })

  it('holds what the shell said until the view attaches, then hands it over without marks', () => {
    const shell = new ScriptedShell(() => undefined)
    const terminal = start(shell)
    shell.say(`${PROMPT}hello`)
    const shown: string[] = []

    terminal.attach(text => shown.push(text))
    shell.say(' world')

    expect(shown).toEqual(['PS C:\\work> hello', ' world'])
  })

  it('ends a command left running when its shell goes, and says it exited', async () => {
    const shell = new ScriptedShell((line, say) => {
      say(`${line}\r\n${mark('C')}half`)
    })
    const terminal = start(shell)
    const exits: number[] = []
    terminal.onExit(code => exits.push(code))
    shell.say(PROMPT)
    const running = terminal.run('long')
    await new Promise(resolve => setTimeout(resolve, 20))

    shell.kill()

    expect((await running).status).toBe('unfinished')
    expect(exits).toEqual([1])
    expect(terminal.busy).toBe(true)
  })
})

describe("a terminal's nonce", () => {
  it('is 32 hex characters, a new one each time, as the server takes it', () => {
    const one = newNonce()

    expect(one).toMatch(/^[0-9a-f]{32}$/)
    expect(newNonce()).not.toBe(one)
  })
})
