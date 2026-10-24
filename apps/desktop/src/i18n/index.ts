import { merge, type Override } from './catalog'
import { en, type Messages } from './en'
import { ptBR } from './pt-BR'

export type Locale = 'en' | 'pt-BR'

/**
 * The screen's language, taken from the system:
 * Electron gives the page the system's languages (`app.getLocale()`), the first one Atlas has
 * wins, and anything else is English. Any Portuguese reads the Brazilian catalog.
 */
export function pickLocale(languages: readonly string[]): Locale {
  for (const language of languages) {
    const lower = language.toLowerCase()

    if (lower === 'pt' || lower.startsWith('pt-')) {
      return 'pt-BR'
    }

    if (lower === 'en' || lower.startsWith('en-')) {
      return 'en'
    }
  }

  return 'en'
}

const OVERRIDES: Record<Locale, Override<Messages> | undefined> = { en: undefined, 'pt-BR': ptBR }

export function messagesFor(locale: Locale): Messages {
  return merge<Messages>(en, OVERRIDES[locale])
}

export type LocaleChoice = 'system' | Locale

const LOCALE_KEY = 'atlas.locale'

/** The language the user chose on the settings screen (step 15b), kept by this computer; until
 * then, the system's. */
export function chosenLocale(): LocaleChoice {
  const stored = typeof localStorage === 'undefined' ? null : localStorage.getItem(LOCALE_KEY)

  return stored === 'en' || stored === 'pt-BR' ? stored : 'system'
}

/** The system's language, as Atlas has it. */
export const systemLocale: Locale = pickLocale(typeof navigator === 'undefined' ? [] : navigator.languages)

const choice = chosenLocale()

export const locale: Locale = choice === 'system' ? systemLocale : choice

/** Keep a language and reload the window, whose texts are read once as it opens. The server is not
 * touched, so no conversation stops. */
export function setLocale(next: LocaleChoice, reload: () => void): void {
  if (next === 'system') {
    localStorage.removeItem(LOCALE_KEY)
  } else {
    localStorage.setItem(LOCALE_KEY, next)
  }
  reload()
}

/** The screen's texts. */
export const t: Messages = messagesFor(locale)
