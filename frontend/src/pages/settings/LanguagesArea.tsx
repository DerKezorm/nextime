/**
 * Server › General › Languages (TIME-3 AC-5, plan "Einstellungen", Allgemein 5, E48, E51): the card "Sprachen" of the
 * shared module sprachen 1.0.0 (`vendor/nexsprachen`), over `/api/locales`. nextime hands over its name, its English
 * texts (the page makes the template, as nexnotes) and what its i18n does after a change (it asks for the list again).
 */
import { forgetAddedLanguages, SHIPPED } from '../../i18n'
import { LanguagesCard } from '../../vendor/nexsprachen/LanguagesCard'

type Texts = Parameters<typeof LanguagesCard>[0]['template']

export function LanguagesArea() {
  return <LanguagesCard app="nextime" base="/api/locales" template={SHIPPED.en.texts as Texts} onChange={forgetAddedLanguages} />
}
