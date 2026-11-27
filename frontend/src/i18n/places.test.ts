/**
 * Where nextime sends people (plan "Einstellungen", E39): the places in nextime and in the other apps it names are the
 * ones the settings have now. nexpaper and nextasks keep their keys under Connections › API keys; the public address
 * is under Server › Sign-in; the password under My account › Security. The old places and words stand nowhere.
 */
import de from './de.json'
import en from './en.json'
import whatsNewDe from './whatsnew/de.json'
import whatsNewEn from './whatsnew/en.json'

function sentences(tree: unknown): string[] {
  if (typeof tree === 'string') return [tree]
  if (tree && typeof tree === 'object') return Object.values(tree).flatMap(sentences)
  return []
}

const OLD = {
  // E67: "Andere nex-Apps" and "Schnittstellen" left Server › Allgemein for Server › Verbindungen, here and in the partners.
  de: ['Einstellungen › API', 'API-Token', 'Token ändern', 'Speicher und Sicherungen', 'unter Einstellungen ein', 'Einstellungen, Server', 'unter „Mein Konto“', 'Allgemein › Andere nex-Apps', 'Allgemein › Schnittstellen'],
  en: ['Settings › API', 'API token', 'Change token', 'under Settings.', 'Settings, Server', 'under “My account”', 'General › Other nex apps', 'General › Interfaces'],
}

describe('the places nextime names', () => {
  it.each([
    ['de', de],
    ['en', en],
  ] as const)('are the settings of today in %s, never the old ones', (language, texts) => {
    const all = sentences(texts)
    expect(all.filter((text) => OLD[language].some((old) => text.includes(old)))).toEqual([])
  })

  it('send to nexpaper and nextasks under Connections › API keys, with the rights of the key module', () => {
    expect(de.nexpaper.section.how).toContain('in nexpaper unter Mein Konto › Verbindungen › API-Schlüssel einen Schlüssel mit dem Recht „Nur lesen“')
    expect(en.nexpaper.section.how).toContain('in nexpaper under My account › Connections › API keys')
    expect(de.errors.nexpaper_token_invalid).toContain('unter Mein Konto › Verbindungen › API-Schlüssel')
    expect(de.errors.nextasks_token_invalid).toContain('unter Mein Konto › Verbindungen › API-Schlüssel')
    expect(en.errors.nextasks_token_invalid).toContain('under My account › Connections › API keys')
    expect(de.errors.public_url_missing).toContain('unter Server › Anmeldung')
    // E67: the own network and the API of a partner under Server › Verbindungen, there as here.
    expect(de.errors.nextasks_address_local).toContain('unter Server › Verbindungen › Andere nex-Apps')
    expect(en.nextasks.section.localOff).toContain('under Server › Connections › Other nex apps')
    expect(de.errors.nexpaper_api_off).toContain('dort unter Server › Verbindungen › Schnittstellen')
  })
})

/** E46: a place is written group › area (› card): "Mein Konto › Verbindungen › API-Schlüssel", "Server › Allgemein".
 * An area never starts a path and never follows "Einstellungen ›" alone; no commas or arrows between the steps. */
const GROUPS = { de: ['Mein Konto', 'Server'], en: ['My account', 'Server'] }
const AREAS = {
  de: ['Kalender und Konten', 'Auf iPhone und Co', 'Mitnehmen und umziehen', 'Google einrichten', 'Speicher und Sicherung', 'Benachrichtigungen', 'Verbindungen', 'Darstellung', 'Sicherheit', 'Freigaben', 'Allgemein', 'Anmeldung', 'Profil', 'Konten', 'Mail', 'Log'],
  en: ['Calendars and accounts', 'On iPhone and co', 'Take along and move', 'Set up Google', 'Storage and backups', 'Notifications', 'Connections', 'Appearance', 'Security', 'Shares', 'General', 'Sign-in', 'Profile', 'Accounts', 'Mail', 'Log'],
}
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** The paths in a sentence that break the rule. */
export function wrongPlaces(text: string, language: 'de' | 'en'): string[] {
  const wrong: string[] = []
  const groups = GROUPS[language].map(escape).join('|')
  const settings = language === 'de' ? 'Einstellungen' : 'Settings'
  for (const area of AREAS[language]) {
    const name = escape(area)
    // An area right after "›": the step before it must be a group (or another area, a card of it is fine).
    for (const found of text.matchAll(new RegExp(`(\\S[^›,]*?) › ${name}(?![\\w-])`, 'g'))) {
      const before = found[1].trim()
      if (before.endsWith(settings)) wrong.push(found[0])
    }
    // An area that starts a path ("unter Verbindungen ›", "in Allgemein ›", at the start, after a quote or bracket).
    for (const found of text.matchAll(new RegExp(`(^|unter |under |in |[„“"(]|; )(${name}) ›`, 'g'))) {
      const at = (found.index ?? 0) + found[1].length
      const head = text.slice(0, at)
      if (!new RegExp(`(${groups}) › $`).test(head)) wrong.push(`${area} ›`)
    }
    // Commas or arrows between a group and an area.
    if (new RegExp(`(${groups}|${settings})(,| →| ->) ${name}`).test(text)) wrong.push(`… ${area}`)
  }
  return wrong
}

describe('E46: places as group › area', () => {
  it.each([
    ['de', de, whatsNewDe],
    ['en', en, whatsNewEn],
  ] as const)('every path in %s starts with the group, with "›" between the steps', (language, texts, whatsNew) => {
    const found = [...sentences(texts), ...sentences(whatsNew)].flatMap((text) => wrongPlaces(text, language).map((place) => `${place} in: ${text}`))
    expect(found).toEqual([])
  })

  it('finds what breaks the rule', () => {
    expect(wrongPlaces('Lege unter Verbindungen › API-Schlüssel einen an.', 'de')).toEqual(['Verbindungen ›'])
    expect(wrongPlaces('unter Einstellungen › Verbindungen', 'de')).toHaveLength(1)
    expect(wrongPlaces('Richte ihn unter Mein Konto, Sicherheit ein.', 'de')).toEqual(['… Sicherheit'])
    expect(wrongPlaces('unter Mein Konto › Verbindungen › API-Schlüssel', 'de')).toEqual([])
    expect(wrongPlaces('Settings › My account › Connections', 'en')).toEqual([])
    expect(wrongPlaces('under Server › Connections › Interfaces', 'en')).toEqual([])
  })
})

