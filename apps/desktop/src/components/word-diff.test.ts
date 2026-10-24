import { describe, expect, it } from 'vitest'

import { shared, wordDiff } from './word-diff'

describe('a word diff', () => {
  it('marks only the words that change, punctuation apart', () => {
    expect(wordDiff('Lives in Porto; works remotely.', 'Lives in Lisbon; works remotely.')).toEqual([
      { kind: 'same', text: 'Lives in ' },
      { kind: 'removed', text: 'Porto' },
      { kind: 'added', text: 'Lisbon' },
      { kind: 'same', text: '; works remotely.' }
    ])
  })

  it('gives what was added or removed alone, and nothing for equal texts', () => {
    expect(wordDiff('Likes tea.', 'Likes green tea.')).toEqual([
      { kind: 'same', text: 'Likes ' },
      { kind: 'added', text: 'green ' },
      { kind: 'same', text: 'tea.' }
    ])
    expect(wordDiff('Likes green tea.', 'Likes tea.')).toEqual([
      { kind: 'same', text: 'Likes ' },
      { kind: 'removed', text: 'green ' },
      { kind: 'same', text: 'tea.' }
    ])
    expect(wordDiff('Same.', 'Same.')).toEqual([{ kind: 'same', text: 'Same.' }])
  })

  it('joins neighbouring changes across a lone space', () => {
    expect(wordDiff('Works at Acme Corp now.', 'Works at Example Inc now.')).toEqual([
      { kind: 'same', text: 'Works at ' },
      { kind: 'removed', text: 'Acme Corp' },
      { kind: 'added', text: 'Example Inc' },
      { kind: 'same', text: ' now.' }
    ])
  })

  it('rebuilds both texts, and says how little two unrelated ones share', () => {
    const before = 'Prefers short answers.\nUses Linux at home.'
    const after = 'Prefers long answers, with examples.\nUses Windows at home.'
    const pieces = wordDiff(before, after)

    expect(
      pieces
        .filter(piece => piece.kind !== 'added')
        .map(piece => piece.text)
        .join('')
    ).toBe(before)
    expect(
      pieces
        .filter(piece => piece.kind !== 'removed')
        .map(piece => piece.text)
        .join('')
    ).toBe(after)
    expect(shared(wordDiff('Lives in Porto.', 'Drinks coffee daily'))).toBe(0)
    expect(shared(pieces)).toBeGreaterThan(0.5)
  })
})
