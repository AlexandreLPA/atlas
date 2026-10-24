/**
 * The marks a shell writes around each command (step 16f), read from a terminal's output: how the
 * terminal knows a command started, ended, and with which exit code.
 *
 * FinalTerm's OSC 133 marks (`A` the prompt, `C` a command's start, `D;<exit>` its end), each
 * carrying a random nonce, so a program that prints a mark of its own (a log, a `cat` of a script)
 * is not taken for the shell. VS Code's own marks (OSC 633) say only 0 or 1 in PowerShell.
 *
 * Pure: chunks in, text and marks out, in order, a mark cut across two chunks held until whole.
 */

export type Mark = { exit: null | number; kind: 'end' } | { kind: 'prompt' } | { kind: 'start' }

export type Piece = { kind: 'mark'; mark: Mark } | { kind: 'text'; text: string }

const ESC = '\x1b'
const BEL = '\x07'
/** The longest a mark may be, so output that only looks like the start of one is let through. */
const LONGEST_MARK = 256

export class MarkReader {
  private held = ''

  constructor(private readonly nonce: string) {}

  /** A chunk of the terminal's output, as its text (our marks taken out) and our marks, in order. */
  feed(chunk: string): Piece[] {
    let rest = this.held + chunk
    this.held = ''
    const pieces: Piece[] = []
    const text = (value: string) => {
      if (value) {
        pieces.push({ kind: 'text', text: value })
      }
    }

    for (;;) {
      const at = rest.indexOf(`${ESC}]133;`)

      if (at < 0) {
        // An escape at the very end may be a mark's start: held for the next chunk.
        const tail = trailingStart(rest)
        text(rest.slice(0, rest.length - tail.length))
        this.held = tail

        return pieces
      }

      text(rest.slice(0, at))
      const end = terminator(rest, at)

      if (end === null) {
        if (rest.length - at > LONGEST_MARK) {
          text(rest.slice(at, at + 2))
          rest = rest.slice(at + 2)
          continue
        }

        this.held = rest.slice(at)

        return pieces
      }

      const body = rest.slice(at + 6, end.start)
      const mark = this.read(body)

      if (mark) {
        pieces.push({ kind: 'mark', mark })
      } else {
        // Not ours (no nonce, or another's): left for the terminal, which ignores it.
        text(rest.slice(at, end.after))
      }

      rest = rest.slice(end.after)
    }
  }

  private read(body: string): Mark | null {
    const fields = body.split(';')
    const nonce = fields.find(field => field.startsWith('nonce='))?.slice(6)

    if (nonce !== this.nonce) {
      return null
    }

    switch (fields[0]) {
      case 'A':
        return { kind: 'prompt' }
      case 'C':
        return { kind: 'start' }
      case 'D': {
        const code = Number.parseInt(fields[1] ?? '', 10)

        return { exit: Number.isNaN(code) ? null : code, kind: 'end' }
      }
      default:
        return null
    }
  }
}

/** Where a mark ends: BEL, or ESC `\` (ST). */
function terminator(text: string, from: number): null | { after: number; start: number } {
  for (let index = from + 6; index < text.length; index += 1) {
    if (text[index] === BEL) {
      return { after: index + 1, start: index }
    }

    if (text[index] === ESC && text[index + 1] === '\\') {
      return { after: index + 2, start: index }
    }
  }

  return null
}

/** The end of `text` that may be the start of a mark: an escape and what follows it, if it reads as
 * the beginning of `ESC ] 133 ;`. */
function trailingStart(text: string): string {
  const marker = `${ESC}]133;`

  for (let size = Math.min(marker.length - 1, text.length); size > 0; size -= 1) {
    if (marker.startsWith(text.slice(-size))) {
      return text.slice(-size)
    }
  }

  return ''
}

/**
 * A command's output as the conversation shows it and the model reads it: the terminal's escapes
 * taken out (colors, cursor moves, titles), its line endings plain, and a line a program redrew
 * (a progress bar's `\r`) kept as it last looked.
 */
export function plainOutput(raw: string): string {
  /* eslint-disable no-control-regex -- a terminal's escapes are control characters */
  const stripped = raw
    // OSC: a title, a link, a mark of another program's.
    .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
    // Windows' pseudo-terminal (ConPTY) moves the cursor to a line's start where a program wrote
    // a line break: read back as one.
    .replace(/\x1b\[\d+;1H|\x1b\[\d*E/g, '\n')
    // CSI: colors, cursor moves, erasing.
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    // Any other escape and its one character.
    .replace(/\x1b[@-Z\\-_]/g, '')
    .replace(/\r\n/g, '\n')
  /* eslint-enable no-control-regex */

  return stripped
    .split('\n')
    .map(line => line.split('\r').reduce((shown, drawn) => drawn + shown.slice(drawn.length), ''))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
