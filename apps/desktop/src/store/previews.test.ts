import { beforeEach, describe, expect, it } from 'vitest'

import { frameableUrl, sanitizeSandbox } from '@/components/preview-view'

import { $panelTab, $previews, closePreview, openPreview, previewables, targetOf } from './previews'

const tool = (name: string, args: object, result = '') => ({ args: JSON.stringify(args), kind: 'tool', name, result })

describe('the previews (step 16f)', () => {
  beforeEach(() => {
    $previews.set([])
    $panelTab.set(null)
  })

  it('keeps a frame sandboxed whatever spelling asks for more (ALLOW-SAME-ORIGIN too)', () => {
    expect(sanitizeSandbox('allow-scripts')).toBe('allow-scripts')
    expect(sanitizeSandbox('allow-scripts ALLOW-SAME-ORIGIN allow-top-navigation')).toBe('allow-scripts')
    expect(sanitizeSandbox('Allow-Forms allow-scripts')).toBe('allow-forms allow-scripts')
    // Nothing allowed left is the default posture, never an unsandboxed frame.
    expect(sanitizeSandbox('allow-same-origin')).toBe('allow-scripts')
    expect(frameableUrl('http://localhost:5173/')).toBe(true)
    expect(frameableUrl('file:///C:/secret.html')).toBe(false)
    expect(frameableUrl('javascript:alert(1)')).toBe(false)
  })

  it('opens a tab for the conversation that asked, once per source, and closes one or all of its own', () => {
    const page = { content: '<h1>Hi</h1>', kind: 'artifact' as const, label: 'HTML', language: 'html' }
    const first = openPreview('c-1', page, true)
    const again = openPreview('c-1', page, true)
    openPreview('c-2', targetOf('http://localhost:3000'), false)

    expect(again.id).toBe(first.id)
    expect($panelTab.get()).toBe(first.id)
    expect($previews.get().map(tab => tab.owner)).toEqual(['c-1', 'c-2'])
    // A preview of another conversation never takes the panel.
    expect($panelTab.get()).toBe(first.id)

    closePreview('c-2', { target: 'http://localhost:3000' })
    expect($previews.get().map(tab => tab.owner)).toEqual(['c-1'])
    closePreview('c-1')
    expect($previews.get()).toEqual([])
  })

  it('finds the files written and the sites started, the latest four', () => {
    const items = [
      tool('write_file', { path: 'a.html' }),
      tool('write_file', { path: 'notes.txt' }),
      tool('run_command', { command: 'npm run dev' }, 'ready on http://localhost:5173/ in 300ms'),
      tool('edit_file', { path: 'docs/b.md' }),
      tool('write_file', { path: 'c.svg' }),
      tool('run_command', { command: 'serve' }, 'Listening on http://0.0.0.0:8080.'),
      tool('write_file', { path: 'a.html' })
    ]

    expect(previewables(items).map(target => target.path ?? target.url)).toEqual([
      'docs/b.md',
      'c.svg',
      'http://localhost:8080',
      'a.html'
    ])
  })
})
