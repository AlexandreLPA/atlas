import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { hasTopic, joinTopics } from '../src/lib/news-topics'

describe('a news row and its topics', () => {
  const row = joinTopics(['agents', 'open source', 'vibe coding'])

  it('matches a topic with a space as a whole', () => {
    expect(hasTopic(row, 'open source')).toBe(true)
    expect(hasTopic(row, 'vibe coding')).toBe(true)
  })

  it('matches a one-word topic', () => {
    expect(hasTopic(row, 'agents')).toBe(true)
  })

  it('does not match part of a topic, nor a topic the row lacks', () => {
    expect(hasTopic(row, 'open')).toBe(false)
    expect(hasTopic(row, 'source')).toBe(false)
    expect(hasTopic(row, 'models')).toBe(false)
    expect(hasTopic(joinTopics([]), 'agents')).toBe(false)
  })
})

describe('the published news topics', () => {
  const folder = fileURLToPath(new URL('../src/content/news/', import.meta.url))
  const topics = readdirSync(folder)
    .filter(name => name.endsWith('.md') && !name.startsWith('_'))
    .flatMap(name => {
      const text = readFileSync(`${folder}${name}`, 'utf8')
      const line = /^topics:\s*\[(.*)\]\s*$/m.exec(text)?.[1] ?? ''
      return line
        .split(',')
        .map(topic => topic.trim().replace(/^['"]|['"]$/g, ''))
        .filter(Boolean)
    })

  it.each([...new Set(topics)])('"%s" is found again in a row that carries it', topic => {
    expect(hasTopic(joinTopics(['other', topic]), topic)).toBe(true)
  })
})
