import { describe, expect, it } from 'vitest'

import { CAPS_LOCK, type ClipboardLike, type Keys, pasteText, REPEAT_MS, RESTORE_MS, Shortcuts } from './desktop-powers'

/** The system's shortcuts, held in a map, some taken by another program. */
function system(taken: string[] = []) {
  const held = new Map<string, () => void>()
  const log: string[] = []

  return {
    held,
    log,
    shortcuts: {
      register: (accelerator: string, run: () => void) => {
        if (accelerator === 'Nope') {
          throw new Error('conversion failure')
        }

        if (taken.includes(accelerator) || held.has(accelerator)) {
          return false
        }

        log.push(`+${accelerator}`)
        held.set(accelerator, run)

        return true
      },
      unregister: (accelerator: string) => {
        log.push(`-${accelerator}`)
        held.delete(accelerator)
      }
    }
  }
}

function keys(paste: 'blocked' | 'pasted' = 'pasted', onPaste: () => void = () => undefined) {
  const done: string[] = []
  const made: Keys = {
    close: () => undefined,
    paste: () => {
      done.push('paste')
      onPaste()

      return Promise.resolve(paste)
    },
    tapCapsLock: () => {
      done.push('caps')

      return Promise.resolve()
    }
  }

  return { done, keys: made }
}

describe("a screen's shortcut, heard in every program (step 17b)", () => {
  it('is held, says why it cannot be, replaces its own key and lets go', () => {
    const { held, shortcuts } = system(['F8'])
    const pressed: string[] = []
    const all = new Shortcuts(shortcuts, null, key => pressed.push(key))

    expect(all.set('voice:dictate', 'F9')).toBe('')
    expect(all.set('other:x', 'F9')).toContain("another plugin's shortcut")
    expect(all.set('other:x', 'F8')).toContain('taken by another program')
    expect(all.set('other:x', 'Nope')).toContain('not a key the system knows')
    held.get('F9')?.()
    expect(pressed).toEqual(['voice:dictate'])

    expect(all.set('voice:dictate', 'F10')).toBe('')
    expect([...held.keys()]).toEqual(['F10'])
    expect(all.set('voice:dictate', '')).toBe('')
    expect(held.size).toBe(0)
  })

  it('hears a key held down, repeating, once', () => {
    const { held, shortcuts } = system()
    let now = 1_000
    const pressed: string[] = []
    const all = new Shortcuts(
      shortcuts,
      null,
      key => pressed.push(key),
      () => now
    )
    all.set('voice:dictate', 'F9')

    held.get('F9')?.()
    now += REPEAT_MS - 1
    held.get('F9')?.()
    now += REPEAT_MS
    held.get('F9')?.()

    expect(pressed).toHaveLength(2)
  })

  it('puts the capitals back after Caps Lock, its own press unheard', async () => {
    const { held, log, shortcuts } = system()
    const { done, keys: pressedKeys } = keys()
    const pressed: string[] = []
    const all = new Shortcuts(shortcuts, pressedKeys, key => pressed.push(key))
    all.set('voice:dictate', CAPS_LOCK)

    held.get(CAPS_LOCK)?.()
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(pressed).toEqual(['voice:dictate'])
    expect(done).toEqual(['caps'])
    // Let go while the key is pressed for the user, then held again.
    expect(log).toEqual([`+${CAPS_LOCK}`, `-${CAPS_LOCK}`, `+${CAPS_LOCK}`])
    expect(held.has(CAPS_LOCK)).toBe(true)
  })

  it('lets every key go at once', () => {
    const { held, shortcuts } = system()
    const all = new Shortcuts(shortcuts, null, () => undefined)
    all.set('a:x', 'F1')
    all.set('b:y', 'F2')

    all.clear()

    expect(held.size).toBe(0)
  })
})

/** A clipboard with text and HTML, which notes what it was given. */
function clipboard(text = 'what the user copied') {
  const state = { html: '<b>copied</b>', text, writes: 0 }
  const made: ClipboardLike = {
    availableFormats: () => ['text/plain', 'text/html'],
    readHTML: () => state.html,
    readImage: () => ({ isEmpty: () => true }),
    readRTF: () => '',
    readText: () => state.text,
    write: data => {
      state.writes += 1
      state.text = data.text ?? ''
      state.html = data.html ?? ''
    },
    writeText: given => {
      state.text = given
      state.html = ''
    }
  }

  return { clipboard: made, state }
}

describe('a text pasted where the user is (step 17b)', () => {
  const noWait = () => Promise.resolve()

  it('is typed in the app when its window has the focus, the clipboard untouched', async () => {
    const { clipboard: board, state } = clipboard()
    const typed: string[] = []
    const { done, keys: pressed } = keys()

    const where = await pasteText('Buy bread.', {
      clipboard: board,
      keys: pressed,
      typeInApp: text => typed.push(text) > 0,
      wait: noWait
    })

    expect(where).toBe('app')
    expect(typed).toEqual(['Buy bread.'])
    expect(done).toEqual([])
    expect(state.text).toBe('what the user copied')
  })

  it('is pasted in the program in focus, and the clipboard is put back', async () => {
    const { clipboard: board, state } = clipboard()
    let onClipboard = ''
    const { keys: pressed } = keys('pasted', () => {
      onClipboard = state.text
    })
    const waited: number[] = []

    const where = await pasteText('Buy bread.', {
      clipboard: board,
      keys: pressed,
      typeInApp: () => false,
      wait: ms => {
        waited.push(ms)

        return Promise.resolve()
      }
    })

    expect(where).toBe('pasted')
    expect(onClipboard).toBe('Buy bread.')
    expect(waited).toEqual([RESTORE_MS])
    expect(state).toMatchObject({ html: '<b>copied</b>', text: 'what the user copied' })
  })

  it('stays on the clipboard when the program takes no keys, or none can be pressed', async () => {
    for (const pressed of [keys('blocked').keys, null]) {
      const { clipboard: board, state } = clipboard()

      const where = await pasteText('Buy bread.', {
        clipboard: board,
        keys: pressed,
        typeInApp: () => false,
        wait: noWait
      })

      expect(where).toBe('copied')
      expect(state.text).toBe('Buy bread.')
    }
  })

  it('leaves what the user copied meanwhile', async () => {
    const { clipboard: board, state } = clipboard()

    await pasteText('Buy bread.', {
      clipboard: board,
      keys: keys().keys,
      typeInApp: () => false,
      wait: () => {
        state.text = 'copied meanwhile'

        return Promise.resolve()
      }
    })

    expect(state.text).toBe('copied meanwhile')
    expect(state.writes).toBe(0)
  })
})
