/**
 * Languages. English and German ship with the app; the operator can add more as JSON files, which the
 * server offers under `/api/locales` (the shared module sprachen 1.0.0, `vendor/nexsprachen`: `ownLanguages`).
 *
 * English is always loaded and is the fallback: an added language may be incomplete, and whatever it leaves out
 * appears in English instead of as a raw key. An added file for `en` or `de` is laid over the shipped texts, so a
 * wording can be changed without a release.
 *
 * Texts are rendered as text by React. No translation is ever put into the page as HTML, because an added file
 * comes from outside the code.
 *
 * The texts of sign-in through providers (`oidc.…`) come with the shared module `vendor/nexoidc`, the same words in
 * every nex app; they are merged in here, `{{app}}` says "nextime". So are those of accounts (`account.…`, the shared
 * module `vendor/nexkonten`) and of system mail (`mail.…`, `vendor/nexpost`).
 */

import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import { byName, ownLanguages, type LanguageOption } from '../vendor/nexsprachen/languages'
import { CODE } from '../vendor/nexsprachen/sprachen'

import { kontenVariables } from '../vendor/nexkonten/konten'
import kontenDe from '../vendor/nexkonten/konten.de.json'
import kontenEn from '../vendor/nexkonten/konten.en.json'
import { postVariables } from '../vendor/nexpost/post'
import postDe from '../vendor/nexpost/post.de.json'
import postEn from '../vendor/nexpost/post.en.json'
import { oidcVariables, oidcWording } from '../vendor/nexoidc/oidc'
import oidcDe from '../vendor/nexoidc/oidc.de.json'
import oidcEn from '../vendor/nexoidc/oidc.en.json'
import ownDe from './de.json'
import ownEn from './en.json'

type Texts = Record<string, unknown>

/** nextime's own texts with the shared sign-in texts. */
const en: Texts = { ...ownEn, ...oidcWording(oidcEn, { singleAccount: false, invitations: true }), ...kontenEn, ...postEn }
const de: Texts = { ...ownDe, ...oidcWording(oidcDe, { singleAccount: false, invitations: true }), ...kontenDe, ...postDe }
/** What `{{app}}` stands for in the shared texts. */
export const APP_NAME = 'nextime'

export const SHIPPED: Record<string, { name: string; texts: Texts }> = {
  en: { name: 'English', texts: en },
  de: { name: 'Deutsch', texts: de },
}

export type { LanguageOption }

const FALLBACK = 'en'
const STORAGE_KEY = 'nextime.language'

/** The operator's languages from the shared module sprachen 1.0.0 (`/api/locales`, readable before signing in). */
export const own = ownLanguages({ base: '/api/locales' })
/** The languages nextime ships, as the module wants them for the language choice. */
export const BUILT_IN = Object.entries(SHIPPED).map(([code, entry]) => ({ code, name: entry.name }))

/** After the operator added or removed one: ask the server again. */
export function forgetAddedLanguages(): void {
  own.forget()
}

/** Shipped texts first, the operator's file on top. False when there is nothing for this code at all. */
async function loadTexts(code: string): Promise<boolean> {
  const texts = await own.texts(code)
  if (texts) i18n.addResourceBundle(code, 'translation', texts, true, true)
  return code in SHIPPED || texts !== null
}

export { byName }

/** The language choice of the profile: Deutsch, English, and every added one in its place among them by name. */
export function languageOptions(): Promise<LanguageOption[]> {
  return own.options(BUILT_IN)
}

function storedLanguage(): string | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return stored && CODE.test(stored) ? stored : null
  } catch {
    return null
  }
}

/** The first of the browser's languages nextime ships (only the language part counts: `de-AT` is `de`); English
 * for any other (TIME-3 AC-9). */
export function firstShipped(wanted: readonly string[]): string {
  for (const language of wanted) {
    const primary = language.toLowerCase().split(/[-_]/)[0]
    if (primary in SHIPPED) return primary
  }
  return FALLBACK
}

/** The browser's language as nextime speaks it: for the first visit, the sign-in, a read link. */
export function browserLanguage(): string {
  return firstShipped(navigator.languages ?? [navigator.language])
}

// The shipped languages at once, so the first picture is never raw keys; an added one follows from the server.
const first = storedLanguage() ?? browserLanguage()
i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, de: { translation: de } },
  lng: first in SHIPPED ? first : FALLBACK,
  fallbackLng: FALLBACK,
  interpolation: {
    escapeValue: false,
    defaultVariables: { ...oidcVariables(APP_NAME), ...postVariables(APP_NAME), ...kontenVariables(APP_NAME) },
  },
  returnNull: false,
})
document.documentElement.lang = i18n.language
if (!(first in SHIPPED)) void changeLanguage(first, false)
else void loadTexts(first).then(() => i18n.changeLanguage(first))

/** Switches the page to a language; false when nextime has none by that code. */
export async function changeLanguage(code: string, remember = true): Promise<boolean> {
  if (!(await loadTexts(code))) return false
  if (remember) {
    try {
      localStorage.setItem(STORAGE_KEY, code)
    } catch {
      // Then the choice only holds until the next reload.
    }
  }
  document.documentElement.lang = code
  await i18n.changeLanguage(code)
  return true
}

export default i18n
