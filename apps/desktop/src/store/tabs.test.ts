import { beforeEach, describe, expect, it } from 'vitest'

import type { OpenSession } from './app'
import { readTabs, samePlace, screenPlace, type TabPlace, Tabs, writeTabs } from './tabs'

const chat = (conversationId: string): TabPlace => ({ conversationId, kind: 'conversation' })

const session = (fields: Partial<OpenSession> = {}): OpenSession => ({
  choice: null,
  elsewhere: '',
  executor: 'claude-code',
  expired: false,
  folder: '/work/shop',
  instanceId: '',
  sessionId: '',
  title: '',
  ...fields
})

const titles = (tabs: Tabs) => tabs.$tabs.get().map(tab => `${tab.preview ? '~' : ''}${tab.title}`)

beforeEach(() => {
  localStorage.clear()
})

describe('the tabs of a home', () => {
  it('open a row in the passing tab, which the next row takes, and keep the tab that was used', () => {
    const tabs = new Tabs()

    tabs.show(chat('a'), 'A')
    tabs.show(chat('b'), 'B')
    expect(titles(tabs)).toEqual(['~B'])

    tabs.keep(chat('b'))
    tabs.show(chat('c'), 'C')
    expect(titles(tabs)).toEqual(['B', '~C'])

    // A tab already open shows itself; the passing one stays where it is.
    tabs.show(chat('b'), 'B')
    expect(titles(tabs)).toEqual(['B', '~C'])
  })

  it('give what a first message made a tab of its own at the end, the passing one left alone', () => {
    const tabs = new Tabs()
    tabs.show(chat('a'), 'A')

    tabs.made(chat('new'), 'New')

    expect(titles(tabs)).toEqual(['~A', 'New'])
  })

  it('close to the next tab, else the one before, else to a new conversation', () => {
    const tabs = new Tabs()
    for (const id of ['a', 'b', 'c']) {
      tabs.made(chat(id), id.toUpperCase())
    }
    const [a, b, c] = tabs.$tabs.get()

    expect(tabs.close(b?.id ?? '')).toBe(c)
    expect(tabs.close(c?.id ?? '')).toBe(a)
    expect(tabs.close(a?.id ?? '')).toBeNull()
    expect(tabs.$tabs.get()).toEqual([])
  })

  it('follow the session on screen as it is named, and give a new one a tab once it starts', () => {
    const tabs = new Tabs()
    const fresh = session()
    const started = session({ instanceId: 'i1' })

    tabs.follow(fresh, started)
    expect(titles(tabs)).toEqual([''])

    const named = session({ instanceId: 'i1', sessionId: 's1', title: 'Fix the cart' })
    tabs.follow(started, named)
    expect(titles(tabs)).toEqual(['Fix the cart'])
    expect(tabs.find({ kind: 'session', session: session({ sessionId: 's1' }) })).not.toBeNull()

    // Another account takes it on: the same tab.
    tabs.follow(
      named,
      session({ executor: 'claude-code:work', instanceId: 'i2', sessionId: 's2', title: 'Fix the cart' })
    )
    expect(tabs.$tabs.get()).toHaveLength(1)
  })

  it('tell a session by its id in its harness, else by its instance here', () => {
    expect(
      samePlace(
        { kind: 'session', session: session({ sessionId: 's1' }) },
        { kind: 'session', session: session({ instanceId: 'i9', sessionId: 's1' }) }
      )
    ).toBe(true)
    expect(
      samePlace(
        { kind: 'session', session: session({ instanceId: 'i1' }) },
        { kind: 'session', session: session({ instanceId: 'i2' }) }
      )
    ).toBe(false)
    expect(samePlace(chat('a'), { kind: 'session', session: session({ instanceId: 'a' }) })).toBe(false)
  })

  it('say which tab the screen shows: none for a new conversation, a new session or a page', () => {
    expect(screenPlace('conversation', 'a', null)).toEqual(chat('a'))
    expect(screenPlace('conversation', null, null)).toBeNull()
    expect(screenPlace('session', 'a', session())).toBeNull()
    expect(screenPlace('session', 'a', session({ sessionId: 's1' }))).toEqual({
      kind: 'session',
      session: session({ sessionId: 's1' })
    })
    expect(screenPlace('routines', 'a', null)).toBeNull()
  })

  it('move a dragged tab, retitle them from the lists and forget what is gone', () => {
    const tabs = new Tabs()
    for (const id of ['a', 'b', 'c']) {
      tabs.made(chat(id), id.toUpperCase())
    }

    tabs.move(tabs.$tabs.get()[2]?.id ?? '', 0)
    expect(titles(tabs)).toEqual(['C', 'A', 'B'])

    tabs.retitle(place => (place.kind === 'conversation' && place.conversationId === 'a' ? 'Groceries' : undefined))
    expect(titles(tabs)).toEqual(['C', 'Groceries', 'B'])

    tabs.forget(place => place.kind === 'conversation' && place.conversationId === 'c')
    expect(titles(tabs)).toEqual(['Groceries', 'B'])
  })
})

describe('the tabs the window keeps', () => {
  it('come back as they were, the one on screen too, and a closed one never does', () => {
    const tabs = new Tabs()
    for (const id of ['a', 'b', 'c']) {
      tabs.made(chat(id), id.toUpperCase())
    }
    tabs.show(chat('d'), 'D')
    tabs.close(tabs.$tabs.get()[1]?.id ?? '')
    writeTabs('local/default', tabs.saved(chat('c')))

    const again = new Tabs()
    const shown = again.restore(readTabs('local/default'))

    expect(titles(again)).toEqual(['A', 'C', '~D'])
    expect(shown?.place).toEqual(chat('c'))
  })

  it('are kept apart per home, and an entry this version cannot read is left out', () => {
    const tabs = new Tabs()
    tabs.made(chat('a'), 'A')
    writeTabs('local/default', tabs.saved(chat('a')))
    writeTabs('local/work', { active: null, tabs: [] })
    const kept = JSON.parse(localStorage.getItem('atlas.tabs') ?? '{}') as Record<string, unknown>
    localStorage.setItem(
      'atlas.tabs',
      JSON.stringify({
        ...kept,
        'local/old': {
          active: 1,
          tabs: [{ place: { kind: 'board' } }, { place: chat('b'), preview: false, title: 'B' }]
        }
      })
    )

    expect(readTabs('local/default')?.tabs.map(tab => tab.title)).toEqual(['A'])
    expect(readTabs('local/work')).toEqual({ active: null, tabs: [] })
    expect(readTabs('local/old')).toEqual({ active: 0, tabs: [{ place: chat('b'), preview: false, title: 'B' }] })
    expect(readTabs('local/none')).toBeNull()
  })

  it('come back before the tabs opened meanwhile', () => {
    const tabs = new Tabs()
    tabs.made(chat('a'), 'A')
    writeTabs('local/default', tabs.saved(null))

    const again = new Tabs()
    again.show(chat('b'), 'B')
    again.restore(readTabs('local/default'))

    expect(titles(again)).toEqual(['A', '~B'])
  })
})
