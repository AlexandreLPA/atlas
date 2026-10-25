import { describe, expect, it } from 'vitest'

import { byDay, formatDay, parseLog } from '../src/lib/changelog'

const sha = (c: string) => c.repeat(40)

describe('parseLog', () => {
  it('reads each commit with its subject and its Portuguese note', () => {
    const output = `${sha('a')}\x1f2026-10-25\x1fThe agent schedules a routine\x1fO agente agenda uma rotina\x1e\n${sha('b')}\x1f2026-10-24\x1fA subject with no note\x1f\x1e\n`
    expect(parseLog(output)).toEqual([
      {
        sha: sha('a'),
        date: '2026-10-25',
        subject: 'The agent schedules a routine',
        subjectPt: 'O agente agenda uma rotina'
      },
      { sha: sha('b'), date: '2026-10-24', subject: 'A subject with no note', subjectPt: '' }
    ])
  })

  it('skips what is not a commit', () => {
    expect(parseLog('garbage\x1e\x1e')).toEqual([])
  })
})

describe('byDay', () => {
  it('groups changes by day, keeping their order', () => {
    const changes = [
      { sha: sha('a'), date: '2026-10-25', subject: 'One', subjectPt: '' },
      { sha: sha('b'), date: '2026-10-25', subject: 'Two', subjectPt: '' },
      { sha: sha('c'), date: '2026-10-24', subject: 'Three', subjectPt: '' }
    ]
    expect(byDay(changes).map(day => [day.date, day.changes.map(c => c.subject)])).toEqual([
      ['2026-10-25', ['One', 'Two']],
      ['2026-10-24', ['Three']]
    ])
  })

  it('writes a day out in full', () => {
    expect(formatDay('2026-10-25')).toBe('25 October 2026')
  })
})
