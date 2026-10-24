import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { diffRows, ReviewRow } from './review-row'

afterEach(cleanup)

const DIFF = [
  '--- a/SKILL.md',
  '+++ b/SKILL.md',
  '@@ -4,3 +4,3 @@',
  ' Steps:',
  '-7. When it arrives, relay the result, and say what is pending.',
  '+7. When it arrives, verify then report the result, and say what is pending.',
  ' 8. Stop.',
  '@@ -20 +20 @@',
  '-Old end.',
  '+Brand new.',
  '--- /dev/null',
  '+++ b/references/hosts.md',
  '@@ -0,0 +1 @@',
  '+One host.',
  "[Atlas: 12 more line(s); the skill's folder has the rest.]"
].join('\n')

describe('a skill diff as rows', () => {
  it('names each file once, puts a gap between hunks and keeps the note', () => {
    const rows = diffRows(DIFF)

    expect(rows.map(row => row.kind)).toEqual([
      'file',
      'same',
      'minus',
      'plus',
      'same',
      'gap',
      'minus',
      'plus',
      'file',
      'plus',
      'note'
    ])
    expect(rows[0]?.text).toBe('SKILL.md')
    expect(rows[8]?.text).toBe('references/hosts.md')
    expect(rows.at(-1)?.text).toContain('12 more line(s)')
  })

  it('marks only the words a rewritten line changed', () => {
    const rows = diffRows(DIFF)
    const [minus, plus] = [rows[2], rows[3]]
    const part = (row: typeof minus) => row?.text.slice(...(row.changed ?? [0, 0]))

    expect(part(minus)).toBe('relay')
    expect(part(plus)).toBe('verify then report')
    // Lines with nothing in common are not marked, and neither is a line with no pair.
    expect(rows[6]?.changed).toBeUndefined()
    expect(rows[9]?.changed).toBeUndefined()
  })

  it('marks whole words', () => {
    const [minus, plus] = diffRows('-Write in bullets.\n+Write in bullets, at most five.')
    const [short, long] = diffRows('-Relay the result.\n+Check the file, then relay the result.')

    expect(minus?.text.slice(...(minus.changed ?? [0, 0]))).toBe('bullets.')
    expect(plus?.text.slice(...(plus.changed ?? [0, 0]))).toBe('bullets, at most five.')
    expect(short?.text.slice(...(short.changed ?? [0, 0]))).toBe('Relay')
    expect(long?.text.slice(...(long.changed ?? [0, 0]))).toBe('Check the file, then relay')
  })

  it('names a removed file by the path it had', () => {
    expect(diffRows('--- a/references/old.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-Old.')[0]).toEqual({
      kind: 'file',
      text: 'references/old.md'
    })
  })
})

describe("the review's row", () => {
  const review = {
    actions: ['Memory updated', "Skill 'notes' updated", "Skill 'tips' created"],
    diffs: {
      "Skill 'notes' updated": DIFF,
      "Skill 'tips' created": '--- /dev/null\n+++ b/SKILL.md\n@@ -0,0 +1 @@\n+Tip.'
    },
    key: 'r1',
    kind: 'review' as const,
    proposals: [],
    text: "Memory updated · Skill 'notes' updated · Skill 'tips' created"
  }

  it("opens a skill's diff in a click, one at a time, and closes it in another", () => {
    // jsdom lays nothing out, so it has no scrollIntoView; the real app's test sees the diff.
    Element.prototype.scrollIntoView = () => undefined
    render(<ReviewRow review={review} />)

    expect(screen.getByTestId('review-summary').textContent).toBe(review.text)
    expect(screen.queryByRole('button', { name: 'Memory updated' })).toBeNull()
    expect(screen.queryByTestId('skill-diff')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: "Skill 'notes' updated" }))

    expect(screen.getByTestId('skill-diff').textContent).toContain('verify then report')
    expect(screen.getByTestId('skill-diff').querySelector('mark')?.textContent).toBe('relay')

    fireEvent.click(screen.getByRole('button', { name: "Skill 'tips' created" }))

    expect(screen.getAllByTestId('skill-diff')).toHaveLength(1)
    expect(screen.getByTestId('skill-diff').textContent).toContain('Tip.')

    fireEvent.click(screen.getByRole('button', { name: "Skill 'tips' created" }))

    expect(screen.queryByTestId('skill-diff')).toBeNull()
  })
})
