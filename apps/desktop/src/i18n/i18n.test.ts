import { describe, expect, it } from 'vitest'

import { en } from './en'
import { ptBR } from './pt-BR'

import { locale, messagesFor, pickLocale } from './index'

/** Every leaf's path in a catalog: `composer.send`, `ask.title`... */
function leaves(value: unknown, prefix = ''): string[] {
  if (typeof value !== 'object' || value === null) {
    return [prefix]
  }

  return Object.entries(value).flatMap(([key, inner]) => leaves(inner, prefix ? `${prefix}.${key}` : key))
}

describe('the screen language', () => {
  it('comes from the system, Portuguese for any pt, English for the rest', () => {
    expect(pickLocale(['pt-BR', 'en-US'])).toBe('pt-BR')
    expect(pickLocale(['pt-PT'])).toBe('pt-BR')
    expect(pickLocale(['pt'])).toBe('pt-BR')
    expect(pickLocale(['en-GB', 'pt-BR'])).toBe('en')
    expect(pickLocale(['fr-FR', 'pt-BR'])).toBe('pt-BR')
    expect(pickLocale(['fr-FR'])).toBe('en')
    expect(pickLocale([])).toBe('en')
    expect(locale).toBe('en') // jsdom's navigator says en-US
  })

  it('has every text in Portuguese, a complete locale holding every key', () => {
    expect(leaves(ptBR).sort()).toEqual(leaves(en).sort())
  })

  it('merges over English and keeps the functions', () => {
    const pt = messagesFor('pt-BR')
    expect(pt.composer.send).toBe('Enviar')
    expect(pt.ask.title(3)).toBe('O agente faz 3 perguntas')
    expect(pt.composer.effortLevel('xhigh')).toBe('Muito alto')
    expect(messagesFor('en').composer.effortLevel('none')).toBe('Off')
    expect(messagesFor('en').composer.effortLevel('ultra')).toBe('ultra')
  })
})
