/**
 * Every shipped text is asked for somewhere, numbers count right, and English is English.
 * "Download template" (Settings, General) hands every key to translators: a text no page shows is work for nothing,
 * and a sentence of another app (a vault, notes, a task) told them nextime had it.
 *
 * A key counts as asked for when the code names it, or names its start in a built key (`notices.${kind}`,
 * `accounts.confirm.${kind}.title`). The error texts are checked against the server's codes in
 * `backend/tests/test_texts.py`.
 */

import sharedEn from '../vendor/nexoidc/oidc.en.json'
import de from './de.json'
import en from './en.json'

/** The sentences the shared sign-in blueprint fixes word for word: not ours to change. */
const sharedErrors: string[] = Object.values(sharedEn.oidc.error)

type Files = {
  readFileSync: (file: string, encoding: string) => string
  readdirSync: (folder: string, options: { recursive: true }) => string[]
}
const fsName = 'node:fs'
const files = (await import(/* @vite-ignore */ fsName)) as Files

function flat(tree: Record<string, unknown>, prefix = ''): string[] {
  return Object.entries(tree).flatMap(([key, value]) =>
    value && typeof value === 'object' ? flat(value as Record<string, unknown>, `${prefix}${key}.`) : [prefix + key],
  )
}

const source = files
  .readdirSync('src', { recursive: true })
  .map((name) => name.replaceAll('\\', '/'))
  // The shared sign-in module (`vendor/nexoidc`) brings its own texts and is checked by its own test.
  .filter((name) => /\.tsx?$/.test(name) && !name.includes('.test.') && !name.startsWith('i18n/') && !name.startsWith('vendor/'))
  .map((name) => files.readFileSync(`src/${name}`, 'utf-8'))
  .join('\n')

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function askedFor(key: string): boolean {
  const base = key.replace(/_(one|other|zero)$/, '')
  if (source.includes(base)) return true
  // A key with a built end: `auth.reset.${key}Title` asks for doneTitle, expiredTitle and invalidTitle.
  const stem = base.replace(/[A-Z][a-z]*$/, '')
  if (stem !== base && new RegExp(`['\`]${escape(stem)}\\$\\{`).test(source)) return true
  // A key built from a fixed start and a value: `server.confirm.backup_${kind}.title`.
  for (let end = base.length - 1; end > 0; end--) {
    if (source.includes('`' + base.slice(0, end) + '${')) return true
  }
  const parts = base.split('.')
  for (let end = parts.length - 1; end > 0; end--) {
    const start = parts.slice(0, end).join('.') + '.'
    if (new RegExp(`['\`]${escape(start)}\\$\\{`).test(source)) return true
  }
  return false
}

const text = (tree: unknown, key: string) => String(key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)[part], tree))

describe('the shipped texts', () => {
  it('are all asked for by some page', () => {
    const unused = flat(de).filter((key) => !key.startsWith('errors.') && !askedFor(key))
    expect(unused).toEqual([])
  })

  it('have every key a page asks for, in both languages', () => {
    // A key named whole in the code: t('server.restore'), i18nKey="…", or a constant handed to t() later (label:
    // 'settings.tabs.account'). Built keys (`server.restoreTitles.${kind}`) are left to the check above. A deleted
    // key showed its bare name as a label.
    const sections = new Set(Object.keys(de))
    const keys = new Set(flat(de))
    const english = new Set(flat(en))
    const has = (set: Set<string>, key: string) => set.has(key) || set.has(`${key}_one`) || set.has(`${key}_other`)
    const asked = [...source.matchAll(/['"`]([a-z][A-Za-z0-9]*(?:\.[A-Za-z0-9_]+)+)['"`]/g)]
      .map((m) => m[1])
      .filter((key) => sections.has(key.split('.')[0]) && !/\.(tsx?|json|svg|png|css|js)$/.test(key))
    expect(asked.length).toBeGreaterThan(250)
    expect([...new Set(asked.filter((key) => !has(keys, key) || !has(english, key)))]).toEqual([])
  })

  it('speak English, not German word for word', () => {
    // "Angeben" is enter, "anlegen" is create, "enthalten" is contain, "abgelaufen" is expired.
    const stiff = flat(en).filter((key) =>
      /\bGive (a|an|the|your)\b|\bMake (a|an|one|them|new|it)\b|\bmakes? (a|an|new|its|their)\b|\b(runs?|ran) out\b|\bWhole:|\bhangs on\b|\bthe own\b|\bTake (the )?second factor\b/i.test(text(en, key)) ||
      // "holds" for "gilt" or "enthält"; a key one holds down while dragging stays ("Hold while dragging").
      /\bholds?\b|\bHolds\b/.test(text(en, key)),
    ).filter((key) => !sharedErrors.includes(text(en, key)))
    expect(stiff.map((key) => `${key}: ${text(en, key)}`)).toEqual([])
  })

  it('count with a word for one, never "1 Personen"', () => {
    // A text with {{count}} and a noun after it has its _one form.
    const counted = flat(de).filter((key) => !/_(one|other|zero)$/.test(key) && /\{\{count\}\} \p{Lu}/u.test(text(de, key)))
    expect(counted).toEqual([])
    expect(text(de, 'accounts.countEvents_one')).toBe('{{count}} Termin')
  })

  it('speak of nextime only', () => {
    const raw = [de, en].map((tree) => JSON.stringify(tree)).join('\n')
    expect(raw.match(/notes\.example\.com|wie in nexlore|as in nexlore|nextrmnl|nexcanvas|Whiteboard|Give an address/g)).toBeNull()
  })
})
