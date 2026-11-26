/**
 * Server › General › Languages (TIME-3 AC-5, plan "Einstellungen", Allgemein 5, E48, E51): the card "Sprachen" of the
 * shared module sprachen 1.0.1 (`vendor/nexsprachen`), over `/api/locales`. nextime hands over its name, its English
 * texts (the page makes the template, as nexnotes), what its i18n does after a change (it asks for the list again) and
 * its way to the server: every request names this tab in `X-Nextime-Client`, without which a change is refused
 * (`client_required`), as the module sicherung's client does.
 */
import { tabId } from '../../api/client'
import { forgetAddedLanguages, SHIPPED } from '../../i18n'
import { LanguagesCard } from '../../vendor/nexsprachen/LanguagesCard'
import type { Fetcher } from '../../vendor/nexsprachen/sprachen'

type Texts = Parameters<typeof LanguagesCard>[0]['template']

/** nextime's way to the server for the card: its cookies and the tab's name. */
// eslint-disable-next-line react-refresh/only-export-components
export const sprachenFetch: Fetcher = (path, init) => {
  const headers = new Headers(init.headers)
  headers.set('X-Nextime-Client', tabId())
  return fetch(path, { credentials: 'same-origin', ...init, headers })
}

export function LanguagesArea() {
  return <LanguagesCard app="nextime" base="/api/locales" template={SHIPPED.en.texts as Texts} onChange={forgetAddedLanguages} fetcher={sprachenFetch} />
}
