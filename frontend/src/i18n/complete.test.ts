/** The two shipped languages have the same keys, the same placeholders, and no dashes as pauses. */

import { NEXUI_TEXTS } from '../vendor/nexui'
import de from './de.json'
import en from './en.json'

function flat(tree: Record<string, unknown>, prefix = ''): Map<string, string> {
  const out = new Map<string, string>()
  for (const [key, value] of Object.entries(tree)) {
    if (value && typeof value === 'object') for (const [k, v] of flat(value as Record<string, unknown>, `${prefix}${key}.`)) out.set(k, v)
    else out.set(prefix + key, String(value))
  }
  return out
}

const german = flat(de)
const english = flat(en)

describe('shipped languages', () => {
  it('have exactly the same keys', () => {
    expect([...german.keys()].filter((key) => !english.has(key))).toEqual([])
    expect([...english.keys()].filter((key) => !german.has(key))).toEqual([])
  })

  it('use the same placeholders for each key', () => {
    const holes = (text: string) => [...text.matchAll(/\{\{(\w+)\}\}/g)].map((m) => m[1]).sort()
    const different = [...german].filter(([key, text]) => JSON.stringify(holes(text)) !== JSON.stringify(holes(english.get(key) ?? '')))
    expect(different.map(([key]) => key)).toEqual([])
  })

  it('never pause with a dash', () => {
    const dashed = [...german, ...english].filter(([, text]) => /[–—]/.test(text))
    expect(dashed.map(([key]) => key)).toEqual([])
  })

  it('say trash in English, never bin', () => {
    expect([...english].filter(([, text]) => /\bbins?\b/i.test(text)).map(([key, text]) => `${key}: ${text}`)).toEqual([])
    // Floor: the trash is still spoken of, so the check above has something to look at.
    expect([...english].filter(([, text]) => /\btrash\b/i.test(text)).length).toBeGreaterThan(0)
  })

  it('count with figures throughout the backup line, never a figure next to a word', () => {
    // The line of the backups is the module sicherung's now; nextime's own count of files in a restore stays.
    for (const key of ['server.countFiles_one']) {
      expect(german.get(key), key).toContain('{{count}}')
      expect(english.get(key), key).toContain('{{count}}')
    }
  })

  it('call the program version Version in German', () => {
    // "Über nextime" speaks with the ui kit's words (AboutPage, plan "Einstellungen"); nextime's own say the same.
    expect(NEXUI_TEXTS.de.aboutVersion).toBe('Version')
    expect(NEXUI_TEXTS.de.updatesCurrent).toBe('Das ist die neueste Version.')
  })

  it('never speak of boards, spaces or another app', () => {
    // The areas of nextasks keep their name ("Bereiche", asked for while testing, 2026-11-23).
    const foreign = [...german, ...english].filter(([key, text]) => /\bboards?\b|Whiteboard|nexcanvas|nexsuite|\bBereich/i.test(text) && !/^(tasks|nextasks)\./.test(key))
    expect(foreign.map(([key, text]) => `${key}: ${text}`)).toEqual([])
  })
})
