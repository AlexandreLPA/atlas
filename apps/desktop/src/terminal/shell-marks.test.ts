import { describe, expect, it } from 'vitest'

import { MarkReader, plainOutput } from './shell-marks'

const N = 'f00d'
const mark = (body: string) => `\x1b]133;${body};nonce=${N}\x07`

describe("a shell's marks (step 16f, OSC 133 with a nonce)", () => {
  it('reads the prompt, a start and an end with its exit code, between the text', () => {
    const reader = new MarkReader(N)

    expect(reader.feed(`${mark('A')}PS C:\\> ${mark('C')}hello\r\n${mark('D;3')}`)).toEqual([
      { kind: 'mark', mark: { kind: 'prompt' } },
      { kind: 'text', text: 'PS C:\\> ' },
      { kind: 'mark', mark: { kind: 'start' } },
      { kind: 'text', text: 'hello\r\n' },
      { kind: 'mark', mark: { exit: 3, kind: 'end' } }
    ])
  })

  it('holds a mark cut across two chunks until it is whole', () => {
    const reader = new MarkReader(N)
    const whole = `out${mark('D;0')}next`

    const first = reader.feed(whole.slice(0, 6))
    const second = reader.feed(whole.slice(6))

    expect([...first, ...second]).toEqual([
      { kind: 'text', text: 'out' },
      { kind: 'mark', mark: { exit: 0, kind: 'end' } },
      { kind: 'text', text: 'next' }
    ])
  })

  it('leaves a mark with another nonce to the terminal: a program cannot pass for the shell', () => {
    const reader = new MarkReader(N)
    const forged = '\x1b]133;D;0;nonce=other\x07'

    expect(reader.feed(`log ${forged} more`)).toEqual([
      { kind: 'text', text: 'log ' },
      { kind: 'text', text: forged },
      { kind: 'text', text: ' more' }
    ])
  })

  it('takes the escapes out of an output and keeps a redrawn line as it last looked', () => {
    expect(plainOutput('\x1b[32mok\x1b[0m\r\n10%\r55%\r100%\r\n\x1b]0;title\x07done\r\n')).toBe('ok\n100%\ndone')
    // Windows' ConPTY moves the cursor where a program wrote a line break (cmd's prompt after its
    // output), as seen through node-pty on 15/10/2026.
    expect(plainOutput('atlas-cmd\x1b[7;1HC:\\Temp>\x1b[?25h')).toBe('atlas-cmd\nC:\\Temp>')
  })
})
