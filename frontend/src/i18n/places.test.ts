/**
 * Where nextime sends people (plan "Einstellungen", E39): the places in nextime and in the other apps it names are the
 * ones the settings have now. nexpaper and nextasks keep their keys under Connections › API keys; the public address
 * is under Server › Sign-in; the password under My account › Security. The old places and words stand nowhere.
 */
import de from './de.json'
import en from './en.json'

function sentences(tree: unknown): string[] {
  if (typeof tree === 'string') return [tree]
  if (tree && typeof tree === 'object') return Object.values(tree).flatMap(sentences)
  return []
}

const OLD = {
  de: ['Einstellungen › API', 'API-Token', 'Token ändern', 'Mein Konto › Verbindungen', 'Speicher und Sicherungen', 'unter Einstellungen ein', 'Einstellungen, Server', 'unter „Mein Konto“'],
  en: ['Settings › API', 'API token', 'Change token', 'My account › Connections', 'under Settings.', 'Settings, Server', 'under “My account”'],
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
    expect(de.nexpaper.section.how).toContain('in nexpaper unter Verbindungen › API-Schlüssel einen Schlüssel mit dem Recht „Nur lesen“')
    expect(en.nexpaper.section.how).toContain('in nexpaper under Connections › API keys')
    expect(de.errors.nexpaper_token_invalid).toContain('unter Verbindungen › API-Schlüssel')
    expect(de.errors.nextasks_token_invalid).toContain('unter Verbindungen › API-Schlüssel')
    expect(en.errors.nextasks_token_invalid).toContain('under Connections › API keys')
    expect(de.errors.public_url_missing).toContain('Einstellungen › Server › Anmeldung')
  })
})
