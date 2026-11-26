/** TIME-26 in the interface: Settings › My account › Connections with the card "API-Schlüssel" of the shared key module
 * (schluessel 1.0.0, plan "Einstellungen", E3): the key shown once to copy, the table with right, MCP mark, calendars,
 * run-out and last use, "Widerrufen" after asking, the address for programs, the dialog with 90 days chosen, nextime's
 * notes on hidden calendars and on AI tools that write, a hint while the operator has the API off; and the operator's
 * "API-Schlüssel aller Konten" with "Sperren". */
import App from './App'
import { calendar, calendarList } from './calendarFakes'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, calls, choose, click, KEYS_LISTING, me, ok, render, serve, settle, signedIn, start, stop, type } from './testing'
import type { AdminKey, KeyEntry, KeyListing } from './vendor/nexschluessel/schluessel'

const SHOWN_ONCE = ['nt_api', 'GEHEIM-nur-einmal'].join('_')
const anna = me({ role: 'operator' })
const LISTS = calendarList([calendar(1, 'Persönlich', { default: true }), calendar(2, 'Familie', { color: 'green' })])

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.restoreAllMocks()
})

const key = (id: number, name: string, more: Partial<KeyEntry> = {}): KeyEntry => ({
  id, kind: 'api', name, right: 'read', options: { mcp: false }, areas: null, prefix: 'nt_api_abcd', created_at: '2026-11-20T10:00:00Z',
  last_used_at: null, expires_at: '2027-02-18T10:00:00Z', expired: false, expiring: false, blocked: false, legacy: null, program: false, ...more,
})

function server(keys: KeyEntry[] = [], change: Partial<KeyListing> = {}, admin: AdminKey[] = []) {
  let list = { ...KEYS_LISTING, ...change, keys } as unknown as KeyListing
  const counted = () => ({ ...list, kinds: list.kinds.map((kind) => ({ ...kind, count: list.keys.length })) })
  serve(
    signedIn(anna, (method, path, body) => {
      if (path.startsWith('/api/calendars')) return ok(LISTS)
      if (path === '/api/connections/keys' && method === 'GET') return ok(counted())
      if (path === '/api/connections/keys' && method === 'POST') {
        const sent = body as { name: string; right: string; options: Record<string, boolean> }
        const made = key(9, sent.name, { right: sent.right, options: sent.options })
        list = { ...list, keys: [made, ...list.keys] }
        return { status: 201, body: { key: made, secret: SHOWN_ONCE } }
      }
      if (path.startsWith('/api/connections/keys/') && method === 'DELETE') {
        list = { ...list, keys: list.keys.filter((entry) => `/api/connections/keys/${entry.id}` !== path) }
        // The server answers 204; the stand-in of `fetch` cannot make a 204 with a body.
        return { status: 200, body: null }
      }
      if (path === '/api/connections/admin/keys') return ok({ keys: admin })
      if (path.startsWith('/api/connections/admin/keys/') && method === 'POST') return ok({ ...admin[0], blocked: true })
      return undefined
    }),
  )
}

async function open(where = '/settings/connections'): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const rows = () => [...document.querySelectorAll<HTMLElement>('[data-testid="api-keys-table"] [data-part="table-row"]')]

describe('TIME-26 keys', () => {
  it('TIME-26 EC-17 AC-7 without a key: what keys are for, "Neuer API-Schlüssel", the address and nextime\'s notes', async () => {
    server()
    await open()
    const card = box.querySelector<HTMLElement>('[data-card="api-keys"]')!
    expect(card.textContent).toContain('Ein API-Schlüssel lässt ein Programm mit deinen Kalendern arbeiten')
    expect(one('api-keys-new')!.textContent).toBe('Neuer API-Schlüssel')
    expect((one('api-keys-address')!.querySelector('input') as HTMLInputElement).value).toBe('https://time.example.com/api/v1')
    expect(one('keys-hidden-note')!.textContent).toBe('„Alle Kalender, auch spätere“ heißt: alle, die du einblendest. Ausgeblendete liest ein Schlüssel nur, wenn du sie unter „Nur diese Kalender“ ausdrücklich wählst.')
  })

  it('TIME-26 AC-1 AC-11 the dialog with name, right, calendars, run-out (90 days chosen) and MCP; the key once to copy', async () => {
    server()
    await open()
    await click(one('api-keys-new'))
    const dialog = one('key-dialog')!
    const expiry = dialog.querySelector<HTMLSelectElement>('[data-testid="key-expiry"]')!
    expect(expiry.value).toBe('90')
    expect([...expiry.options].map((option) => option.value)).toEqual(['30', '90', '365', 'never'])
    // nextime's words for its rights and its calendars (TIME-26), the choice in the modules' one wording (M-5).
    expect(dialog.textContent).toContain('Lesen und schreiben')
    expect([one('key-areas-all')!.textContent, one('key-areas-some')!.textContent]).toEqual(['Alle Kalender, auch spätere', 'Nur diese Kalender'])
    type(one('key-name') as HTMLInputElement, 'Planer am Laptop')
    await click(one('key-right-write'))
    await click(one('key-areas-some'))
    await click(one('key-area-2'))
    await click(one('key-option-mcp'))
    await choose(expiry, 'never')
    await click(one('key-submit'))
    const sent = calls.find((call) => call.method === 'POST' && call.path === '/api/connections/keys')!.body as Record<string, unknown>
    expect(sent).toMatchObject({ kind: 'api', name: 'Planer am Laptop', right: 'write', areas: [2], options: { mcp: true }, days: null })
    expect(String(sent.client_ref)).not.toBe('')
    expect((one('key-secret')!.querySelector('input') as HTMLInputElement).value).toBe(SHOWN_ONCE)
    // Never again: opened anew, the key is gone from the page.
    await open()
    expect(box.textContent).not.toContain(SHOWN_ONCE)
  })

  it('TIME-26 AC-4 EC-1 each row: name with the MCP mark, right, calendars, run-out, last use and "Widerrufen" after asking', async () => {
    server([
      key(1, 'nexdeck Eingang', { areas: [{ id: 2, name: 'Familie' }], last_used_at: new Date(Date.now() - 120_000).toISOString() }),
      key(2, 'Planer', { right: 'write', options: { mcp: true }, expires_at: null }),
      key(3, 'Alt', { expired: true, expires_at: '2026-11-01T10:00:00Z' }),
    ])
    await open()
    const found = rows()
    expect(found).toHaveLength(3)
    expect(found[0].textContent).toContain('nexdeck Eingang')
    expect(found[0].textContent).toContain('Familie')
    expect(found[1].textContent).toContain('MCP')
    expect(found[1].textContent).toContain('Lesen und schreiben')
    expect(found[2].textContent).toContain('Alt')
    const revoke = found[1].querySelector<HTMLElement>('[data-testid="key-revoke"]')!
    expect([revoke.dataset.kind, revoke.dataset.size]).toEqual(['danger', 'sm'])
    await click(revoke)
    expect(one('key-revoke-dialog')!.textContent).toContain('Planer')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(one('key-revoke-dialog')!.querySelector<HTMLElement>('[data-kind="danger"]'))
    await settle()
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/connections/keys/2')).toBe(true)
    expect(rows().map((row) => row.textContent?.includes('Planer'))).toEqual([false, false])
  })

  it('TIME-26 AC-5 EC-15 switched off: a hint and nothing new; with 20 keys nothing new either', async () => {
    server([], { api: false, mcp: false })
    await open()
    expect(one('api-keys-off')!.textContent).toContain('Der Betreiber hat die Programmschnittstelle abgeschaltet.')
    expect((one('api-keys-new') as HTMLButtonElement).disabled).toBe(true)
    stop()
    start()
    server(Array.from({ length: 20 }, (_, index) => key(index + 1, `K${index}`)))
    await open()
    expect((one('api-keys-new') as HTMLButtonElement).disabled).toBe(true)
    expect(one('api-keys-count')!.textContent).toBe('20 von 20 Schlüsseln')
  })

  it('TIME-26 EC-16 a second click on "Anlegen" sends the same reference', async () => {
    server()
    await open()
    await click(one('api-keys-new'))
    type(one('key-name') as HTMLInputElement, 'Doppelt')
    const make = one('key-submit') as HTMLButtonElement
    make.click()
    make.click()
    await click(document.body)
    const refs = calls.filter((call) => call.method === 'POST' && call.path === '/api/connections/keys').map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs.length).toBeGreaterThanOrEqual(1)
    expect(new Set(refs).size).toBe(1)
  })

  it('TIME-26 AC-5 the operator sees every key of every account and locks one after asking (E27)', async () => {
    const admin = [{ ...key(5, 'Skript'), account: { id: 3, name: 'anna', display_name: 'Anna', out: false } }] as unknown as AdminKey[]
    server([], {}, admin)
    await open('/settings/accounts')
    const row = all('all-keys-row')[0] ?? document.querySelector<HTMLElement>('[data-testid="all-keys-table"] [data-part="table-row"]')!
    expect(row.textContent).toContain('Anna')
    expect(row.textContent).toContain('Skript')
    await click(row.querySelector<HTMLElement>('[data-testid="all-keys-block"]'))
    await click(one('all-keys-block-dialog')!.querySelector<HTMLElement>('[data-kind="danger"]'))
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/connections/admin/keys/5/block')).toBe(true)
  })
})

describe('TIME-26 author', () => {
  it('TIME-26 AC-10 an event changed through a key names the key in its head', async () => {
    const { views, theWeek, NOW } = await import('./viewFakes')
    const { eventDetail } = await import('./calendarFakes')
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    const rows = theWeek()
    views({ rows, more: (method, path) => (/^\/api\/events\/\d+/.test(path) && method === 'GET' ? ok(eventDetail({ id: rows[0].id, calendar_id: rows[0].calendar_id, title: rows[0].title, updated_via: 'Kurzbefehl' })) : undefined) })
    await open('/')
    await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
    expect(one('panel-head')?.textContent).toContain('zuletzt geändert über Schlüssel „Kurzbefehl“')
    vi.useRealTimers()
  })
})

describe('TIME-26 after the review', () => {
  it('TIME-26 AC-3 a key that writes for MCP: the hint about texts of invitations stands beside the dialog', async () => {
    server()
    await open()
    expect(one('keys-mcp-write')!.textContent).toContain('Texte aus Einladungen fremder Leute')
    expect(one('keys-mcp-write')!.textContent).toContain('Papierkorb')
  })

  it('TIME-26 AC-1 EC-18 the new key stands whole, to copy', async () => {
    server()
    await open()
    await click(one('api-keys-new'))
    type(one('key-name') as HTMLInputElement, 'Telefon')
    await click(one('key-submit'))
    const secret = one('key-secret')!.querySelector('input') as HTMLInputElement
    expect(secret.value).toBe(SHOWN_ONCE)
    expect(secret.readOnly).toBe(true)
    expect(one('key-secret')!.querySelector('[data-icon="Copy"]')).not.toBeNull()
  })
})
