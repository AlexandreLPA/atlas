import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { rewriteImports } from './loader'
import { $contributions, $pluginPage, $screenErrors, contribute, inArea, screenFailed } from './registry'
import { PluginPage } from './view'

afterEach(() => {
  $contributions.set([])
  $pluginPage.set(null)
  $screenErrors.set({})
})

describe("a screen's imports", () => {
  const urls = { '@atlas/plugin-sdk': 'blob:sdk', react: 'blob:react', 'react/jsx-runtime': 'blob:jsx' }

  it('point at the shims of the app’s own modules, in every form', () => {
    const source = [
      "import { host } from '@atlas/plugin-sdk'",
      'import * as React from "react"',
      "import { jsx } from 'react/jsx-runtime'",
      "const later = import('react')"
    ].join('\n')

    expect(rewriteImports(source, urls)).toBe(
      [
        "import { host } from 'blob:sdk'",
        'import * as React from "blob:react"',
        "import { jsx } from 'blob:jsx'",
        "const later = import('blob:react')"
      ].join('\n')
    )
  })

  it('refuse anything else: a screen cannot pull code from an address or a file', () => {
    for (const specifier of ['node:fs', 'https://example.com/x.js', './other.js', 'lodash']) {
      expect(() => rewriteImports(`import x from '${specifier}'`, urls)).toThrow(`It imports ${specifier}`)
    }
  })
})

describe('the contributions', () => {
  it('are named after their plugin, sorted, replaced by key and taken away', () => {
    const takeB = contribute('beta', { area: 'page', id: 'main', render: () => 'b', title: 'B' })
    contribute('alpha', { area: 'page', id: 'main', order: 200, render: () => 'a', title: 'A' })
    contribute('alpha', { area: 'panel', id: 'side', render: () => 'side', title: 'Side' })
    contribute('beta', { area: 'page', id: 'main', render: () => 'b2', title: 'B again' })

    expect(inArea($contributions.get(), 'page').map(item => [item.key, item.title])).toEqual([
      ['beta:main', 'B again'],
      ['alpha:main', 'A']
    ])
    expect(inArea($contributions.get(), 'panel').map(item => item.key)).toEqual(['alpha:side'])

    $pluginPage.set('beta:main')
    takeB()
    expect($contributions.get().some(item => item.key === 'beta:main')).toBe(true)
  })

  it('refuse an area the app does not have, or nothing to render', () => {
    expect(() =>
      contribute('x', { area: 'statusBar' as 'page', id: 'chip', render: () => null, title: 'Chip' })
    ).toThrow('no area statusBar')
    expect(() => contribute('x', { area: 'page', id: 'p', render: 'no' as never, title: 'P' })).toThrow(
      'nothing to render'
    )
  })

  it("keep why a plugin's screen failed until it loads", () => {
    screenFailed('video', 'It imports node:fs.')
    screenFailed('erp', 'Its module took more than 10 s to load.')
    screenFailed('video', null)

    expect($screenErrors.get()).toEqual({ erp: 'Its module took more than 10 s to load.' })
  })
})

describe('a plugin page', () => {
  it('shows what the plugin renders, and a failure in its place without taking the app down', () => {
    contribute('video', { area: 'page', id: 'main', render: () => <p>Renders: 3</p>, title: 'Video' })
    contribute('broken', {
      area: 'page',
      id: 'main',
      render: () => {
        throw new Error('it broke')
      },
      title: 'Broken'
    })
    $pluginPage.set('video:main')
    const { rerender } = render(<PluginPage />)

    expect(screen.getByText('Renders: 3')).toBeTruthy()

    $pluginPage.set('broken:main')
    rerender(<PluginPage />)

    expect(screen.getByTestId('plugin-failed').textContent).toContain('it broke')
  })
})
