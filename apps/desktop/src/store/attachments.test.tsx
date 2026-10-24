import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import { AttachmentChip } from '@/components/attachment-chip'
import { ThreadView } from '@/components/thread-view'
import type { AttachmentLimits, AttachmentRow } from '@/protocol/protocol.generated'

import { $remote } from './app'
import { type AttachmentKind, formatOf, kindOf, overLimit } from './attachments'
import { applyEvent, EMPTY_THREAD, fromHistory } from './thread'

afterEach(cleanup)
beforeAll(() => {
  // jsdom lays nothing out, so it has no scrollIntoView; and there is no Electron bridge, so the
  // server is taken to be another machine's, whose files the window does not read.
  Element.prototype.scrollIntoView = () => undefined
  Object.assign(window, { atlas: { remote: true } })
  $remote.set(true)
})

const LIMITS: AttachmentLimits = {
  audio: 20 * 1024 * 1024,
  document: 50 * 1024 * 1024,
  image: 5_000_000,
  other: 50 * 1024 * 1024,
  part: 4 * 1024 * 1024,
  text: 50 * 1024 * 1024,
  video: 50 * 1024 * 1024
}

const row = (name: string, kind: AttachmentKind, id = name): AttachmentRow => ({
  id,
  kind,
  media_type: 'application/octet-stream',
  name,
  path: `/home/attachments/${id}/${name}`,
  size: 2048
})

describe('what a file is, from here', () => {
  it.each([
    ['report.pdf', '', 'document'],
    ['Contract.DOCX', '', 'document'],
    ['budget.xlsx', '', 'document'],
    ['photo.jpeg', 'image/jpeg', 'image'],
    ['photo.heic', 'image/heic', 'other'],
    ['clip.mov', 'video/quicktime', 'video'],
    ['voice.m4a', 'audio/mp4', 'audio'],
    ['notes.md', 'text/markdown', 'text'],
    ['main.py', '', 'text'],
    ['README', 'text/plain', 'text'],
    ['archive.zip', 'application/zip', 'other']
  ] as const)('%s is %s', (name, type, kind) => {
    expect(kindOf(name, type)).toBe(kind)
  })
})

describe('each format its own mark', () => {
  it('names the format and colors it by its family, so no two families look alike', () => {
    expect(formatOf('a.pdf', 'document')).toEqual({ label: 'PDF', tone: 'pdf' })
    expect(formatOf('a.docx', 'document')).toEqual({ label: 'DOCX', tone: 'word' })
    expect(formatOf('a.xlsx', 'document')).toEqual({ label: 'XLSX', tone: 'data' })
    expect(formatOf('a.csv', 'text')).toEqual({ label: 'CSV', tone: 'data' })
    expect(formatOf('a.pptx', 'document')).toEqual({ label: 'PPTX', tone: 'slides' })
    expect(formatOf('a.epub', 'document')).toEqual({ label: 'EPUB', tone: 'ebook' })
    expect(formatOf('a.md', 'text')).toEqual({ label: 'MD', tone: 'text' })
    expect(formatOf('a.py', 'text')).toEqual({ label: 'PY', tone: 'code' })
    expect(formatOf('a.json', 'text')).toEqual({ label: 'JSON', tone: 'data' })
    expect(formatOf('a.png', 'image')).toEqual({ label: 'PNG', tone: 'image' })
    expect(formatOf('a.mp4', 'video')).toEqual({ label: 'MP4', tone: 'video' })
    expect(formatOf('a.mp3', 'audio')).toEqual({ label: 'MP3', tone: 'audio' })
    expect(formatOf('blob', 'other')).toEqual({ label: 'FILE', tone: 'archive' })
  })

  it('draws the badge in its family color, and a thumbnail for an image', () => {
    render(
      <>
        <AttachmentChip detail="2 KB" kind="document" name="contract.pdf" />
        <AttachmentChip detail="2 KB" kind="document" name="budget.xlsx" />
        <AttachmentChip detail="2 KB" kind="image" name="photo.png" thumbnail="data:image/png;base64,AA==" />
      </>
    )
    const chips = screen.getAllByTestId('attachment-chip')

    expect(chips.map(chip => chip.dataset.tone)).toEqual(['pdf', 'data', 'image'])
    const badges = screen.getAllByTestId('attachment-badge')
    expect(badges[0]?.className).toContain('bg-red-600')
    expect(badges[1]?.className).toContain('bg-emerald-600')
    expect(chips[2]?.querySelector('img')?.getAttribute('src')).toBe('data:image/png;base64,AA==')
  })
})

describe('what the server would refuse', () => {
  it('is refused at the drop, by its kind', () => {
    expect(overLimit('image', 6_000_000, LIMITS)).toBe(5_000_000)
    expect(overLimit('audio', 21 * 1024 * 1024, LIMITS)).toBe(20 * 1024 * 1024)
    expect(overLimit('document', 49 * 1024 * 1024, LIMITS)).toBeNull()
  })
})

describe('a message with files', () => {
  it('shows its chips above its words, live and reopened alike', () => {
    const attachments = [row('contract.pdf', 'document'), row('photo.png', 'image')]
    const live = applyEvent(EMPTY_THREAD, {
      conversation_id: 'c',
      payload: { attachments, kind: 'message', text: 'Compare them' },
      seq: 1,
      type: 'message.start'
    })
    const reopened = fromHistory(
      [
        {
          active: true,
          attachments,
          call_id: null,
          calls: [],
          created_at: '',
          kind: 'message',
          number: 1,
          reasoning: '',
          role: 'user',
          status: null,
          text: 'Compare them',
          tool_name: null
        }
      ],
      false
    )

    expect(live.items[0]).toMatchObject({ attachments, kind: 'user', text: 'Compare them' })
    expect(reopened.items[0]).toMatchObject({ attachments, kind: 'user', text: 'Compare them' })
    render(<ThreadView thread={reopened} />)
    const chips = screen.getAllByTestId('attachment-chip')
    expect(chips.map(chip => chip.dataset.format)).toEqual(['PDF', 'PNG'])
    // The server is elsewhere: its files are not opened here.
    expect(chips[0]?.querySelector('button')?.hasAttribute('disabled')).toBe(true)
  })

  it('sent with no words shows its files alone', () => {
    const thread = fromHistory(
      [
        {
          active: true,
          attachments: [row('deck.pptx', 'document')],
          call_id: null,
          calls: [],
          created_at: '',
          kind: 'message',
          number: 1,
          reasoning: '',
          role: 'user',
          status: null,
          text: '',
          tool_name: null
        }
      ],
      false
    )
    render(<ThreadView thread={thread} />)

    expect(screen.getByTestId('user-message').textContent).toContain('deck.pptx')
    expect(screen.getByTestId('user-message').querySelector('.rounded-2xl')).toBeNull()
  })
})
