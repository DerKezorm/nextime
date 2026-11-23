/** TIME-26 in the interface: Settings › My account › Connections with the keys for programs (built like nexnotes'
 * "API-Token"): the key shown once with "Kopieren", the list with right, MCP mark, calendars, run-out and last use,
 * "Widerrufen", the sentence under the list, the dialog with 90 days chosen, a hint while the operator has the API off;
 * and the operator's overview of how many keys there are. */
import App from './App'
import { calendar, calendarList } from './calendarFakes'
import type { KeyEntry, KeyList, KeyOverview } from './features/keys/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, calls, choose, click, me, ok, render, serve, settle, signedIn, start, stop, type } from './testing'

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
  id, name, level: 'read', mcp: false, calendars: null, prefix: 'nt_api_abcd', created_at: '2026-11-20T10:00:00Z',
  expires_at: '2027-02-18T10:00:00Z', expired: false, last_used_at: null, ...more,
})

function server(keys: KeyEntry[] = [], change: Partial<KeyList> = {}, overview: KeyOverview = { accounts: [], keys: 0, mcp: 0 }) {
  let list: KeyList = { api_allowed: true, mcp_allowed: true, address: 'https://time.example.com', max_keys: 20, expiry_days: [30, 90, 365, null], default_days: 90, keys, ...change }
  serve(
    signedIn(anna, (method, path, body) => {
      if (path.startsWith('/api/calendars')) return ok(LISTS)
      if (path === '/api/keys' && method === 'GET') return ok(list)
      if (path === '/api/keys' && method === 'POST') {
        const sent = body as { name: string; mcp: boolean; level: 'read' | 'write' }
        const made = key(9, sent.name, { mcp: sent.mcp, level: sent.level })
        list = { ...list, keys: [...list.keys, made] }
        return { status: 201, body: { made, token: SHOWN_ONCE } }
      }
      if (path.startsWith('/api/keys/') && method === 'DELETE') {
        list = { ...list, keys: list.keys.filter((entry) => `/api/keys/${entry.id}` !== path) }
        // The server answers 204; the stand-in of `fetch` cannot make a 204 with a body.
        return { status: 200, body: null }
      }
      if (path === '/api/admin/keys') return ok(overview)
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

describe('TIME-26 keys', () => {
  it('TIME-26 EC-17 AC-7 without a key a sentence what keys are for, "Neuer Schlüssel …" and the sentence under the list', async () => {
    server()
    await open()
    expect(one('keys-none')?.textContent).toContain('Mit einem Schlüssel lesen Programme')
    expect(one('keys-new')?.textContent).toBe('Neuer Schlüssel …')
    expect(one('keys-below')?.textContent).toBe('Programmschnittstelle unter /api/v1. Zugangsdaten zu iCloud und Google gibt sie nie heraus.')
    expect(one('keys-address')?.textContent).toBe('https://time.example.com/api/v1')
  })

  it('TIME-26 AC-1 AC-11 the dialog with name, right, calendars, run-out (90 days chosen) and "für MCP"; the key once above the list', async () => {
    server()
    await open()
    await click(one('keys-new'))
    const dialog = one('key-dialog')!
    expect((dialog.querySelector('[data-testid="key-days"]') as HTMLSelectElement).value).toBe('90')
    expect([...(dialog.querySelector('[data-testid="key-days"]') as HTMLSelectElement).options].map((option) => option.textContent)).toEqual(['30 Tage', '90 Tage', '1 Jahr', 'Nie'])
    expect(dialog.textContent).toContain('Auch für MCP')
    type(dialog.querySelector<HTMLInputElement>('input[type="text"], input:not([type])')!, 'Planer am Laptop')
    // MCP first: ticking it chooses "lesen" (after the review); writing is chosen on purpose afterwards.
    await click(one('key-mcp-choice'))
    await click(one('key-level-write'))
    await click(one('key-scope-some'))
    await click(document.querySelector('[data-testid="key-calendar"][data-calendar="2"]'))
    await choose(one('key-days') as HTMLSelectElement, 'never')
    await click(one('key-make'))
    const sent = calls.find((call) => call.method === 'POST' && call.path === '/api/keys')!.body as Record<string, unknown>
    expect(sent).toMatchObject({ name: 'Planer am Laptop', level: 'write', mcp: true, calendar_ids: [2], days: null })
    expect(String(sent.client_ref)).toMatch(/^ref-/)
    expect(one('keys-made')?.textContent).toContain('Neuer Schlüssel „Planer am Laptop“, nur jetzt sichtbar:')
    expect(one('keys-secret')?.textContent).toBe(SHOWN_ONCE)
    expect(one('keys-copy')?.textContent).toBe('Kopieren')
    // Never again: opened anew, the key is gone from the page.
    await open()
    expect(box.textContent).not.toContain(SHOWN_ONCE)
  })

  it('TIME-26 AC-4 EC-1 each row: name with the MCP mark, right, calendars, run-out, last use and "Widerrufen"', async () => {
    server([
      key(1, 'nexdeck Eingang', { calendars: [{ id: 2, name: 'Familie' }], last_used_at: new Date(Date.now() - 120_000).toISOString() }),
      key(2, 'Planer', { level: 'write', mcp: true, expires_at: null }),
      key(3, 'Alt', { expired: true, expires_at: '2026-11-01T10:00:00Z' }),
    ])
    await open()
    const rows = all('key-row')
    expect(rows[0].querySelector('[data-testid="key-mcp"]')).toBeNull()
    expect(rows[0].querySelector('[data-testid="key-meta"]')?.textContent).toBe('lesen · Familie')
    expect(rows[0].querySelector('[data-testid="key-used"]')?.textContent).toBe('zuletzt benutzt vor 2 Minuten')
    expect(rows[1].querySelector('[data-testid="key-mcp"]')?.textContent).toBe('MCP')
    expect(rows[1].querySelector('[data-testid="key-meta"]')?.textContent).toBe('lesen, schreiben · alle')
    expect(rows[1].querySelector('[data-testid="key-expiry"]')?.textContent).toBe('läuft nie ab')
    expect(rows[1].querySelector('[data-testid="key-used"]')?.textContent).toBe('zuletzt benutzt noch nie')
    expect(rows[2].querySelector('[data-testid="key-expiry"]')?.textContent).toBe('abgelaufen')
    expect(rows[2].querySelector('[data-testid="key-revoke"]')?.textContent).toBe('Entfernen')
    await click(rows[1].querySelector('[data-testid="key-revoke"]'))
    expect(document.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe('Schlüssel „Planer“ widerrufen?')
    await click(one('keys-revoke-confirm'))
    await settle()
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/keys/2')).toBe(true)
    expect(all('key-row').map((row) => row.querySelector('[data-testid="key-name"]')?.textContent)).toEqual(['nexdeck Eingang', 'Alt'])
  })

  it('TIME-26 AC-5 EC-15 switched off: a hint and nothing new; with 20 keys nothing new either', async () => {
    server([], { api_allowed: false, mcp_allowed: false })
    await open()
    expect(one('keys-off')?.textContent).toContain('Der Betreiber hat die Programmschnittstelle abgeschaltet.')
    expect((one('keys-new') as HTMLButtonElement).disabled).toBe(true)
    stop()
    start()
    server(Array.from({ length: 20 }, (_, index) => key(index + 1, `K${index}`)))
    await open()
    expect((one('keys-new') as HTMLButtonElement).disabled).toBe(true)
    expect(box.textContent).toContain('20 von 20 Schlüsseln')
  })

  it('TIME-26 EC-16 a second click on "Anlegen" sends the same reference, and the button waits', async () => {
    server()
    await open()
    await click(one('keys-new'))
    type(one('key-dialog')!.querySelector<HTMLInputElement>('input')!, 'Doppelt')
    const make = one('key-make') as HTMLButtonElement
    make.click()
    make.click()
    await click(document.body)
    const refs = calls.filter((call) => call.method === 'POST' && call.path === '/api/keys').map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs.length).toBeGreaterThanOrEqual(1)
    expect(new Set(refs).size).toBe(1)
  })

  it('TIME-26 AC-5 the operator sees how many keys each account holds', async () => {
    server([], {}, { accounts: [{ account: { id: 1, name: 'Anna' }, keys: 3, mcp: 1 }], keys: 3, mcp: 1 })
    await open('/settings/accounts')
    expect(one('all-keys-row')?.textContent).toBe('Anna3 Schlüssel · davon 1 für MCP')
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
  it('TIME-26 AC-3 a key for MCP reads from the start; with writing, a hint about texts of invitations', async () => {
    server()
    await open()
    await click(one('keys-new'))
    await click(one('key-level-write'))
    await click(one('key-mcp-choice'))
    expect((one('key-level-read') as HTMLInputElement).checked).toBe(true)
    expect(one('key-mcp-write-hint')).toBeNull()
    await click(one('key-level-write'))
    expect(one('key-mcp-write-hint')!.textContent).toContain('Texte aus Einladungen fremder Leute')
    expect(one('key-mcp-write-hint')!.textContent).toContain('Papierkorb')
  })

  it('TIME-26 EC-4 the dialog says that "all calendars" leaves hidden ones out', async () => {
    server()
    await open()
    await click(one('keys-new'))
    expect(one('key-hidden-note')!.textContent).toBe('„Alle Kalender“ heißt: alle, die du einblendest. Ausgeblendete liest ein Schlüssel nur, wenn du sie unter „Nur diese …“ ausdrücklich wählst.')
  })

  it('TIME-26 AC-1 EC-18 the new key stands whole and wraps; a copy that fails says so', async () => {
    server()
    await open()
    await click(one('keys-new'))
    type(one('key-dialog')!.querySelector<HTMLInputElement>('input')!, 'Telefon')
    await click(one('key-make'))
    const secret = one('keys-secret')!
    expect(secret.className).not.toContain('truncate')
    expect(secret.className).toContain('break-all')
    Object.defineProperty(document, 'execCommand', { value: () => false, configurable: true })
    await click(one('keys-copy'))
    await settle()
    expect(one('keys-copy-failed')!.textContent).toContain('Kopieren ging nicht')
  })
})
