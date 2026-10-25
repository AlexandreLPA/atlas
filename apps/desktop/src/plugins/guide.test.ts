import { describe, expect, it } from 'vitest'

import guide from '../../../../src/atlas/docs/pages/extending/plugins.md?raw'

import loader from './loader.ts?raw'
import * as sdk from './sdk'

/** Every word inside the guide's code, inline or fenced. */
function said(): Set<string> {
  const spans = guide.match(/```[\s\S]*?```|`[^`\n]+`/g) ?? []

  return new Set(spans.flatMap(span => span.match(/[A-Za-z_]\w*/g) ?? []))
}

/** The members of `ScreenContext`, as loader.ts declares them: a type, so read from its source. */
function contextMembers(): string[] {
  const body = /export interface ScreenContext \{([\s\S]*?)\n\}/.exec(loader)?.[1] ?? ''

  return [...body.matchAll(/^ {2}(?:readonly )?(\w+)\??:/gm)].map(match => match[1] ?? '')
}

describe('the plugin guide, src/atlas/docs/pages/extending/plugins.md', () => {
  it('names everything a screen gets: the module, its host and its context', () => {
    const names = [...Object.keys(sdk), ...Object.keys(sdk.host), ...Object.keys(sdk.host.state), ...contextMembers()]
    const words = said()

    expect(contextMembers()).toContain('shortcut')
    expect(names.filter(name => !words.has(name))).toEqual([])
  })
})
