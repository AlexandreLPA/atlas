import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import type { Item } from '@/store/thread'

import { ThreadView } from './thread-view'
import { clampForDisplay, inputFields, MAX_SHOWN_CHARS, toolTitle } from './tool-row'

afterEach(cleanup)
beforeAll(() => {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => undefined
})

const text = (value: string, role: 'final' | 'interim' | 'open' = 'final'): Item => ({
  key: `t-${role}-${String(value.length)}`,
  kind: 'text',
  role,
  streaming: false,
  text: value
})

const tool = (fields: Partial<Extract<Item, { kind: 'tool' }>>): Item => ({
  args: '{}',
  key: 'tool',
  kind: 'tool',
  name: 'find_files',
  progress: [],
  result: '',
  seconds: null,
  spillPath: null,
  startedAt: null,
  status: 'completed',
  toolId: 't1',
  ...fields
})

describe('a code block in a reply', () => {
  it('can be copied and not downloaded', async () => {
    const block = 'Run it:\n\n```powershell\natlas secrets set ANTHROPIC_API_KEY\n```\n'
    const { container } = render(<ThreadView thread={{ items: [text(block)], running: false }} />)

    expect(await screen.findByTitle('Copy Code')).toBeTruthy()
    expect(container.querySelector('[data-streamdown="code-block-download-button"]')).toBeNull()
  })
})

describe('the trail', () => {
  it('draws one dot per part, colored by its state, and leaves the user off the line', () => {
    const { container } = render(
      <ThreadView
        thread={{
          items: [
            { attachments: [], key: 'u', kind: 'user', steered: false, text: 'go' },
            { key: 'r', kind: 'reasoning', seconds: 4, startedAt: 0, streaming: false, text: '**Reading** the log' },
            text('Let me look.', 'interim'),
            tool({ key: 'a', status: 'completed' }),
            tool({ key: 'b', status: 'failed', toolId: 't2' }),
            tool({ key: 'c', status: 'denied', toolId: 't3' }),
            text('Done.')
          ],
          running: false
        }}
      />
    )

    const dots = [...container.querySelectorAll('[data-testid="part"]')].map(part => part.getAttribute('data-dot'))
    expect(dots).toEqual(['reasoning', 'interim', 'ok', 'failed', 'denied', 'final'])
    const trails = container.querySelectorAll<HTMLElement>('[data-testid="trail"]')
    expect(trails).toHaveLength(1)
    expect(within(trails[0] as HTMLElement).queryByText('go')).toBeNull()
  })

  it('says what a reasoning is about, and how long it took', () => {
    render(
      <ThreadView
        thread={{
          items: [
            { key: 'r', kind: 'reasoning', seconds: 4, startedAt: 0, streaming: false, text: '**Reading** the log' }
          ],
          running: false
        }}
      />
    )

    expect(screen.getByTestId('reasoning').querySelector('summary')?.textContent).toBe(
      `${t.thread.thoughtFor(t.elapsed(4))} · Reading`
    )
  })

  it('reopened, a reasoning only says it thought', () => {
    const reasoning: Item = { key: 'r', kind: 'reasoning', seconds: null, startedAt: null, streaming: false, text: 'x' }
    render(<ThreadView thread={{ items: [reasoning], running: false }} />)

    expect(screen.getByTestId('reasoning').querySelector('summary')?.textContent).toBe(`${t.thread.thought} · x`)
  })

  it('shows an event it does not know as its name and its payload', () => {
    const other: Item = { key: 'o', kind: 'other', payload: { a: 1 }, type: 'browser.preview' }
    render(<ThreadView thread={{ items: [other], running: false }} />)

    const shown = screen.getByTestId('other-part')
    expect(shown.textContent).toContain('browser.preview')
    expect(shown.textContent).toContain('"a": 1')
  })

  it('says why a turn failed in the words the server sent, not only the kind of error', () => {
    const said = 'There is no key for OpenAI. Store it with `atlas secrets set OPENAI_API_KEY`.'
    const end: Item = { error: 'ModelKeyError', key: 'e', kind: 'end', status: 'failed', text: said }
    render(<ThreadView thread={{ items: [end], running: false }} />)

    expect(screen.getByTestId('turn-end').textContent).toBe(t.thread.failed(said))
  })
})

describe('a tool', () => {
  it('opens on IN and OUT, one argument bare and several under their names', () => {
    expect(inputFields('{"path":"a.md"}')).toEqual([{ name: 'path', value: 'a.md' }])
    expect(inputFields('{"path":"a.md","limit":5}')).toEqual([
      { name: 'path', value: 'a.md' },
      { name: 'limit', value: '5' }
    ])
    expect(inputFields('not json')).toEqual([{ name: '', value: 'not json' }])
    expect(inputFields('{}')).toEqual([])
  })

  it("titles Atlas's own tools by what they do, and leaves the others generic", () => {
    expect(toolTitle('run_command', '{"command":"uv run pytest -q\\necho"}', false)).toEqual({
      action: t.tool.titles.run_command.done,
      target: 'uv run pytest -q'
    })
    expect(toolTitle('read_file', '{"path":"a.md"}', true)).toEqual({
      action: t.tool.titles.read_file.pending,
      target: 'a.md'
    })
    expect(toolTitle('web_search', '{"query":"kv cache"}', false)).toEqual({
      action: t.tool.titles.web_search.done,
      target: '“kv cache”'
    })
    expect(toolTitle('mcp__notes__add', '{"text":"x"}', false)).toBeNull()
  })

  it('cuts a long output at 20,000 characters and copies it whole', () => {
    const long = 'x'.repeat(MAX_SHOWN_CHARS + 5)
    render(<ThreadView thread={{ items: [tool({ result: long })], running: false }} />)

    expect(clampForDisplay(long).startsWith('x'.repeat(MAX_SHOWN_CHARS))).toBe(true)
    expect(clampForDisplay(long)).toContain(t.tool.clamped('5'))
    expect(screen.getByRole('button', { name: t.tool.copyAll })).toBeTruthy()
  })

  it('says how it ended: a failure with its time, a refusal without one, success only its time', () => {
    render(
      <ThreadView
        thread={{
          items: [
            tool({ key: 'a', seconds: 2, status: 'completed' }),
            tool({ key: 'b', seconds: 1, status: 'failed', toolId: 't2' }),
            tool({ key: 'c', status: 'denied', toolId: 't3' })
          ],
          running: false
        }}
      />
    )

    expect(screen.getAllByTestId('tool-state').map(state => state.textContent)).toEqual([
      t.duration(2),
      `${t.tool.failed} · ${t.duration(1)}`,
      t.tool.denied
    ])
  })
})

describe('a skill the user invoked', () => {
  it('shows the /name they typed, marked as a skill loaded', () => {
    const items: Item[] = [
      { attachments: [], key: 'u', kind: 'user', skill: 'weekly-report', steered: false, text: '/weekly-report May' }
    ]
    render(<ThreadView thread={{ items, running: false }} />)

    expect(screen.getByTestId('user-message').textContent).toContain('/weekly-report May')
    expect(screen.getByTestId('skill-invoked').textContent).toBe(t.thread.skill)
    expect(screen.getByTestId('skill-invoked').getAttribute('data-skill')).toBe('weekly-report')
  })
})

describe('following the reply', () => {
  /** jsdom lays nothing out: the thread's height, window and place are set by hand. */
  function scrollTo(top: number) {
    const thread = screen.getByTestId('thread')
    Object.defineProperty(thread, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(thread, 'clientHeight', { configurable: true, value: 200 })
    thread.scrollTop = top
    fireEvent.scroll(thread)
  }

  it('stays still while the user reads above, and a click brings them back down', () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const { rerender } = render(<ThreadView thread={{ items: [text('a')], running: true }} />)

    scrollTo(800)
    expect(screen.queryByTestId('to-latest')).toBeNull()
    scrollTo(300)
    scroll.mockClear()
    rerender(<ThreadView thread={{ items: [text('a'), tool({})], running: true }} />)
    expect(scroll).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: t.thread.toLatest }))
    expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'end' })
    rerender(<ThreadView thread={{ items: [text('a'), tool({}), text('done')], running: true }} />)
    expect(scroll).toHaveBeenLastCalledWith({ block: 'end' })
    Element.prototype.scrollIntoView = () => undefined
  })

  it('follows again when the user sends a message', () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const { rerender } = render(<ThreadView thread={{ items: [text('a')], running: true }} />)

    scrollTo(800)
    scrollTo(300)
    scroll.mockClear()
    const sent: Item = { attachments: [], key: 'u', kind: 'user', steered: true, text: 'and this' }
    rerender(<ThreadView thread={{ items: [text('a'), sent], running: true }} />)
    expect(scroll).toHaveBeenCalledWith({ block: 'end' })
    Element.prototype.scrollIntoView = () => undefined
  })
})
