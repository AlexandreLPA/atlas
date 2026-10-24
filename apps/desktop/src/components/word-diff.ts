/** A run of text the two versions share, or one only the old or the new has. */
export type Piece = { kind: 'added' | 'removed' | 'same'; text: string }

/**
 * What changed from `before` to `after`, word by word, so a card can mark only the words a change
 * touches instead of making the user compare two whole texts. Punctuation is a word of its own, so
 * "Porto;" to "Lisbon;" marks only the city. Each run of changes gives its removed text, then its
 * added text. Past `LIMIT` the texts are compared as wholes, which only costs the detail.
 */
export function wordDiff(before: string, after: string): Piece[] {
  const a = words(before)
  const b = words(after)

  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) {
    start++
  }
  let end = 0
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) {
    end++
  }

  const ops: Piece[] = [
    ...a.slice(0, start).map(text => same(text)),
    ...middle(a.slice(start, a.length - end), b.slice(start, b.length - end)),
    ...a.slice(a.length - end).map(text => same(text))
  ]

  return group(bridge(ops))
}

/** How much of `after` the two versions share, from 0 to 1: little means the diff reads worse
 * than the two texts apart. */
export function shared(pieces: Piece[]): number {
  const kept = pieces.filter(piece => piece.kind === 'same' && piece.text.trim()).reduce((n, p) => n + p.text.length, 0)
  const total = pieces.filter(piece => piece.kind !== 'removed').reduce((n, p) => n + p.text.length, 0)

  return total ? kept / total : 0
}

/** The most cells the word table may have (the changed words of one times the other's). */
const LIMIT = 250_000

function words(text: string): string[] {
  return text.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) ?? []
}

function same(text: string): Piece {
  return { kind: 'same', text }
}

/** The longest common subsequence of the words between the shared start and end. */
function middle(a: string[], b: string[]): Piece[] {
  if (a.length * b.length > LIMIT) {
    return [
      ...a.map(text => ({ kind: 'removed' as const, text })),
      ...b.map(text => ({ kind: 'added' as const, text }))
    ]
  }

  const width = b.length + 1
  // longest[i * width + j]: the longest common subsequence of a[i..] and b[j..].
  const longest = new Uint32Array((a.length + 1) * width)
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      longest[i * width + j] =
        a[i] === b[j]
          ? (longest[(i + 1) * width + j + 1] ?? 0) + 1
          : Math.max(longest[(i + 1) * width + j] ?? 0, longest[i * width + j + 1] ?? 0)
    }
  }

  const ops: Piece[] = []
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      ops.push(same(a[i] ?? ''))
      i++
      j++
    } else if (
      j < b.length &&
      (i === a.length || (longest[i * width + j + 1] ?? 0) >= (longest[(i + 1) * width + j] ?? 0))
    ) {
      ops.push({ kind: 'added', text: b[j] ?? '' })
      j++
    } else {
      ops.push({ kind: 'removed', text: a[i] ?? '' })
      i++
    }
  }

  return ops
}

/** A space alone between two changes joins them, so "a b" to "c d" reads as one change, not two
 * with a kept space between. */
function bridge(ops: Piece[]): Piece[] {
  return ops.flatMap((op, index) => {
    const lone =
      op.kind === 'same' &&
      !op.text.trim() &&
      index > 0 &&
      ops[index - 1]?.kind !== 'same' &&
      ops[index + 1] !== undefined &&
      ops[index + 1]?.kind !== 'same'

    return lone
      ? [
          { kind: 'removed' as const, text: op.text },
          { kind: 'added' as const, text: op.text }
        ]
      : [op]
  })
}

/** Neighbouring words of a kind as one piece, each run of changes as its removed then its added. */
function group(ops: Piece[]): Piece[] {
  const pieces: Piece[] = []
  let removed = ''
  let added = ''

  const flush = () => {
    if (removed) {
      pieces.push({ kind: 'removed', text: removed })
    }
    if (added) {
      pieces.push({ kind: 'added', text: added })
    }
    removed = ''
    added = ''
  }

  for (const op of ops) {
    if (op.kind === 'removed') {
      removed += op.text
    } else if (op.kind === 'added') {
      added += op.text
    } else {
      flush()
      const last = pieces.at(-1)
      if (last?.kind === 'same') {
        last.text += op.text
      } else {
        pieces.push(same(op.text))
      }
    }
  }
  flush()

  return pieces
}
