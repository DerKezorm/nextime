/** TIME-1 in the interface: the setup page, the HTTPS hint for the operator, and the settings' group "Server" with its
 * switches and limits under General, "Ready for the internet?" under Sign-in, the backups under Storage and backups (the
 * module sicherung) and the version under "Über nextime" (plan "Einstellungen"); a member never reaches it. */
import { Route, Routes } from 'react-router-dom'

import App from './App'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, ok, render, serve, settle, signedIn, start, stop, type } from './testing'
import { wholeNumber } from './pages/settings/GeneralCards'
import type { ServerState } from './api/client'
import { INTERNET_POINT } from './pages/settings/readyPoints'

const OPERATOR = me({ id: 1, name: 'tester', display_name: 'Tester', role: 'operator' })

const SETTINGS = {
  public_url: '', password_login: true, two_factor_required: false, backup_schedule: 'daily', backup_time: '03:00', backup_keep: 14,
  smtp_host: '', smtp_port: 587, smtp_security: 'starttls', smtp_user: '', smtp_password_set: false, smtp_from: '',
  upload_max_mb: 25, trash_days: 30, share_links: true, google_enabled: false, google_configured: false, api_enabled: true, connections_local_network: false,
  mcp_enabled: true, address_suggestions: true, update_check: true, data_total: 1000, data_used: 100, data_free: 900,
  nextime_used: 10, nextime_parts: { database: 10 }, data_on_share: false,
}
type Backups = { schedule: { every: string; time: string; keep: number }; zone: string; last: { created: string; size: number; kind: string } | null; count: number; kept: number; failure: { at: string; reason: string } | null; running: boolean; restore_failure: null; restored: null; restore: string; download_password: boolean; backups: unknown[] }
const BACKUPS: Backups = { schedule: { every: 'daily', time: '03:00', keep: 14 }, zone: 'Europe/Berlin', last: null, count: 0, kept: 0, failure: null, running: false, restore_failure: null, restored: null, restore: 'restart', download_password: false, backups: [] }
const STORAGE = { app: 'nextime', used: 10, parts: [{ key: 'database', bytes: 10, within: '', complete: true }], complete: true, total: 1000, disk_used: 100, free: 900, nearly_full: false, where: {}, network: [] }
/** nextime's own points the server adds (E34); none unless a test puts them. */
let ownPoints: { key: string; state: string; title: string; text: string; values: Record<string, unknown> }[] = []
const READY = (https: boolean) => ({ state: https ? 'ok' : 'bad', open: https ? 0 : 1, points: [{ key: 'address', state: https ? 'ok' : 'bad', title: 'account.ready.address.title', text: https ? 'account.ready.address.ok' : 'account.ready.address.bad', values: { address: https ? 'https://time.example.com' : 'http://time.example.com', app: 'nextime' } }, ...ownPoints] })
const STATE: ServerState = { version: '0.1.0', newer: '0.2.0', https: true, internet: true, zone: 'Europe/Berlin' }

function server(state = STATE, settings = SETTINGS, backups = BACKUPS) {
  return signedIn(OPERATOR, (method, path, body) => {
    if (path === '/api/settings' && method === 'GET') return ok(settings)
    if (path === '/api/settings' && method === 'PUT') return ok({ ...settings, ...(body as object) })
    if (path === '/api/server/state') return ok(state)
    if (path === '/api/server/storage') return ok(STORAGE)
    if (path === '/api/server/storage/backups') return ok(backups)
    if (path === '/api/connections/interfaces' && method === 'GET') return ok({ api: settings.api_enabled, mcp: settings.mcp_enabled })
    if (path === '/api/connections/interfaces' && method === 'PUT') return ok({ api: settings.api_enabled, mcp: settings.mcp_enabled, ...(body as object) })
    if (path === '/api/locales') return ok([])
    if (path === '/api/about') return ok({ version: state.version, license: 'AGPL-3.0', repo_url: 'https://example.com/nextime', releases_url: 'https://example.com/nextime/releases', project_url: '' })
    if (path === '/api/about/updates') return ok({ update_check: true, checked: true, latest: state.newer, newer: !!state.newer, checked_at: null, release_url: null })
    if (path === '/api/server/sign-in') return ok({ password_login: true, factor_required: false, passkeys_allowed: true, password_may_go_off: false, factor_may_go_on: false, public_url: '', operator_has_password: true })
    if (path === '/api/server/sign-in/readiness') return ok(READY(state.https))
    if (path.startsWith('/api/oidc/admin/providers')) return ok([])
    if (path === '/api/oidc/authentik/address') return ok({ public_url: '', seen: 'http://localhost', stale: null })
    return undefined
  })
}

beforeEach(async () => {
  start()
  ownPoints = []
  await i18n.changeLanguage('de')
})
afterEach(stop)

describe('TIME-1 the setup', () => {
  const SETUP = { needs_setup: true, code_required: true, signed_in: false, version: '0.1.0', min_password: 12, data_on_share: false }

  it('TIME-1 AC-1 shows "nextime einrichten" with code, display name, username, address, password and "Einrichten"', async () => {
    serve((_method, path) => (path === '/api/setup' ? ok({ ...SETUP, https: true }) : undefined))
    await render(<AuthProvider><App /></AuthProvider>, '/setup')
    expect(box.textContent).toContain('nextime einrichten')
    // The fields of the shared account module (konten, `SCREENS.setup`), each joined to its label.
    const labels = [...box.querySelectorAll('label')].map((label) => label.textContent)
    expect(labels).toEqual(expect.arrayContaining(['Einrichtungscode', 'Anzeigename', 'Benutzername', 'E-Mail', 'Passwort', 'Passwort wiederholen']))
    expect(byText('Einrichten', box)).toBeDefined()
    expect(box.querySelector('[role="status"], .text-warn-500')?.textContent ?? '').not.toContain('HTTPS')
  })

  it('TIME-1 EC-4 any page leads to the setup while nextime has no account', async () => {
    serve((_method, path) => (path === '/api/setup' ? ok(SETUP) : undefined))
    await render(<AuthProvider><App /></AuthProvider>, '/month')
    expect(box.textContent).toContain('nextime einrichten')
  })

  it('TIME-1 EC-4 once set up, the setup page leads to the sign-in', async () => {
    serve((_method, path) => (path === '/api/setup' ? ok({ ...SETUP, needs_setup: false }) : path === '/api/auth/methods' ? ok({ password: true, providers: [] }) : undefined))
    await render(
      <AuthProvider>
        <Routes>
          <Route path="/setup" element={<App />} />
          <Route path="*" element={<App />} />
        </Routes>
      </AuthProvider>,
      '/setup',
    )
    expect(box.textContent).not.toContain('nextime einrichten')
    expect(box.querySelector('input[type="password"]')).not.toBeNull()
  })

  it('TIME-1 AC-6 the setup warns when nextime is not reached over HTTPS', async () => {
    serve((_method, path) => (path === '/api/setup' ? ok({ ...SETUP, https: false }) : undefined))
    await render(<AuthProvider><App /></AuthProvider>, '/setup')
    expect(box.textContent).toContain('nextime ist nicht über HTTPS erreichbar. Kalender auf iPhones und Android verbinden sich erst mit HTTPS.')
  })

  it('TIME-1 AC-1 the setup sends code, name, address and password, then the operator is in', async () => {
    let sent: Record<string, string> | null = null
    serve((method, path, body) => {
      if (path === '/api/setup' && method === 'POST') {
        sent = body as Record<string, string>
        return ok(OPERATOR)
      }
      if (path === '/api/setup') return ok({ ...SETUP, https: true, needs_setup: !sent, signed_in: !!sent })
      if (path === '/api/auth/me') return ok(OPERATOR)
      return undefined
    })
    await render(<AuthProvider><App /></AuthProvider>, '/setup')
    // Code, display name, username (follows the display name), address, password twice (konten `SCREENS.setup`).
    const fields = [...box.querySelectorAll('input')]
    type(fields[0], 'AB12-CD34-EF56')
    type(fields[1], 'Robin')
    type(fields[3], 'robin@example.com')
    type(fields[4], 'twelve chars')
    type(fields[5], 'twelve chars')
    await click(byText('Einrichten', box))
    expect(sent).toMatchObject({ code: 'AB12-CD34-EF56', display_name: 'Robin', name: 'robin', email: 'robin@example.com', password: 'twelve chars' })
    expect(box.querySelector('[data-testid="view-today"]')).not.toBeNull()
  })
})

describe('TIME-1 the group "Server" of the settings', () => {
  it('TIME-1 AC-7 "Über nextime" shows the running version and the newer one beside it; General has no version line', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/about')
    expect(box.querySelector('[data-testid="about-version"]')!.textContent).toBe('0.1.0')
    expect(box.querySelector('[data-testid="about-facts"]')!.textContent).toContain('Version 0.2.0 ist erschienen')
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('[data-testid="server-version"]')).toBeNull()
    expect(box.querySelector('[data-testid="server-newer"]')).toBeNull()
  })

  it('TIME-1 EC-13 without an answer about a newer version only the hint is missing', async () => {
    serve(server({ ...STATE, newer: null }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/about')
    expect(box.querySelector('[data-testid="about-version"]')!.textContent).toBe('0.1.0')
    expect(box.querySelector('[data-testid="about-facts"]')!.textContent).not.toContain('ist erschienen')
    expect(box.querySelector('[role="alert"]')).toBeNull()
  })

  it('TIME-1 AC-5 has the switches; Google says whether it is set up and cannot be switched before; API and MCP are "Schnittstellen"', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('[data-card="features"] h2')!.textContent).toBe('Funktionen für alle Konten')
    const label = (item: HTMLInputElement) => box.querySelector(`label[for="${item.id}"]`)!.textContent
    const switches = [...box.querySelectorAll<HTMLInputElement>('[data-testid="switches"] input[type="checkbox"]')]
    // "Verbindungen ins eigene Netz" for nextasks (TIME-25), off from the start, as nexnotes.
    expect(switches.map(label)).toEqual(['Teilen per Link', 'Verbindungen ins eigene Netz', 'Google-Anbindung (nicht eingerichtet)', 'Adressvorschläge'])
    expect(switches.map((item) => item.checked)).toEqual([true, false, false, true])
    expect(switches[2].disabled).toBe(true)
    await click(switches[3])
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/settings')?.body).toEqual({ address_suggestions: false })
    // "MCP-Zugang" and the API for members: the card "Schnittstellen" of the key module, right after.
    const interfaces = [...box.querySelectorAll<HTMLInputElement>('[data-card="interfaces"] input[type="checkbox"]')]
    expect(interfaces.map(label)).toEqual(['Programmschnittstelle (API) für Mitglieder', 'MCP-Zugang'])
    expect(interfaces.map((item) => item.checked)).toEqual([true, true])
    await click(interfaces[1])
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/connections/interfaces')?.body).toEqual({ mcp: false })
  })

  it('TIME-1 AC-9 General has no box "Zustand" any more: HTTPS stands in "Bereit fürs Internet?" under Sign-in', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('[data-testid="state-https"]')).toBeNull()
    expect(box.querySelector('[data-testid="https-hint"]')).toBeNull()
    expect(box.querySelector('[data-card="state"]')).toBeNull()
    await render(<AuthProvider><App /></AuthProvider>, '/settings/sign-in')
    const ready = box.querySelector<HTMLElement>('[data-card="sign-in-ready"]')!
    expect(ready.textContent).toContain('Öffentliche Adresse mit https')
  })

  it('TIME-1 AC-6 AC-9 without HTTPS the operator sees "Bereit fürs Internet?" in red with the address open', async () => {
    serve(server({ ...STATE, https: false, internet: false }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/sign-in')
    const ready = box.querySelector<HTMLElement>('[data-card="sign-in-ready"]')!
    expect(ready.textContent).toContain('Öffentliche Adresse mit https')
    expect(ready.textContent).toContain('1 Punkt offen')
  })

  it('"Bereit fürs Internet?" (plan "Einstellungen", E34) has nextime\'s own point "Server erreicht das Internet" in nextime\'s words', async () => {
    const shown = async (state: string, text: string, values: Record<string, unknown> = {}) => {
      ownPoints = [{ key: 'internet', state, title: INTERNET_POINT.title, text, values }]
      serve(server())
      await render(<AuthProvider><App /></AuthProvider>, '/settings/sign-in')
      const ready = box.querySelector<HTMLElement>('[data-card="sign-in-ready"]')!
      expect(ready.textContent).toContain('Server erreicht das Internet')
      // Which points are open stays per device: open it only when it is closed.
      const head = box.querySelector<HTMLElement>('#ready-internet [data-part="collapsible-head"]')!
      if (head.getAttribute('aria-expanded') !== 'true') await click(head)
      return box.querySelector('[data-testid="ready-text-internet"]')?.textContent ?? ''
    }
    expect(await shown('ok', INTERNET_POINT.ok)).toContain('iCloud, Google und abonnierte Kalender gleichen ab')
    expect(await shown('bad', INTERNET_POINT.bad)).toContain('kommt gerade nicht ins Internet')
    expect(box.querySelector('[data-testid="ready-summary"]')?.textContent).toBe('1 Punkt offen')
    expect(await shown('bad', INTERNET_POINT.off, { variable: 'NEXTIME_INTERNET_PROBE_URL' })).toContain('NEXTIME_INTERNET_PROBE_URL ist leer')
  })

  it('TIME-1 AC-4 EC-11 a wrong limit is said at the field, nothing is sent, the old value stays', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    const form = box.querySelector('[data-testid="limit-trash"]')!
    const field = form.querySelector('input')!
    expect(field.value).toBe('30')
    // The field's name as nexnotes', the unit in its hint (gallery, E39); the largest attachment keeps "(MB)".
    expect(form.querySelector('label')!.textContent).toBe('Papierkorb leeren nach')
    expect(form.textContent).toContain('In Tagen, von 1 bis 3650.')
    expect(box.querySelector('[data-testid="limit-upload"] label')!.textContent).toBe('Größte Datei je Anhang (MB)')
    expect(box.querySelector('[data-card="features"]')!.textContent).toContain('Was jedes Konto auf diesem Server nutzen darf.')
    for (const wrong of ['0', '-3', '', 'zwölf']) {
      type(field, wrong)
      await click(byText('Speichern', form))
      expect(form.querySelector('[role="alert"]')).not.toBeNull()
      expect(field.getAttribute('aria-invalid')).toBe('true')
    }
    expect(calls.filter((call) => call.method === 'PUT')).toEqual([])
    type(field, '45')
    await click(byText('Speichern', form))
    expect(calls.find((call) => call.method === 'PUT')?.body).toEqual({ trash_days: 45 })
    expect(wholeNumber('7', 1, 3650)).toEqual({ value: 7 })
    expect(wholeNumber(' ', 1, 3650)).toEqual({ problem: 'empty' })
  })

  it('TIME-1 AC-8 the card says rhythm with zone, last backup with time and size, how many are kept', async () => {
    serve(server(STATE, SETTINGS, { ...BACKUPS, last: { created: '2026-11-22T03:00:00+00:00', size: 14 * 1024 * 1024, kind: 'scheduled' } }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    expect(box.querySelector('[data-testid="backup-every-daily"]')!.getAttribute('aria-checked')).toBe('true')
    expect(box.querySelector('[data-card="backups"]')!.textContent).toContain('In der Zeitzone des Servers (Europe/Berlin).')
    expect(box.querySelector('[data-testid="backup-last"]')!.textContent).toMatch(/^Zuletzt: .+, 14,0 MB$/)
    const keep = [...box.querySelectorAll<HTMLInputElement>('[data-card="backups"] input')].find((input) => input.value === '14')
    expect(keep).toBeDefined()
  })

  it('TIME-1 EC-15 the button shows that a backup is running and does not start a second one', async () => {
    serve(server(STATE, SETTINGS, { ...BACKUPS, running: true }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    expect(box.querySelector('[data-testid="backup-last"]')!.textContent).toBe('Sicherung läuft …')
    expect((box.querySelector('[data-testid="backup-now"]') as HTMLButtonElement).disabled).toBe(true)
  })

  it('TIME-1 EC-7 a failed backup is shown with its reason', async () => {
    serve(server(STATE, SETTINGS, { ...BACKUPS, failure: { at: '2026-11-22T02:00:00Z', reason: 'disk_full' } }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    expect(box.querySelector('[data-testid="backup-failure"]')!.textContent).toContain('Der Datenträger ist voll')
  })

  it('TIME-1 AC-8 "Wiederherstellen" checks first and shows what comes, changes and goes before asking', async () => {
    const entry = { name: 'nextime-2026-11-22-030000.zip', size: 1000, created: '2026-11-22T03:00:00+00:00', kind: 'scheduled', note: '', version: '0.1.0', uploaded: false, locked: false, readable: true, facts: { events: 0, files: 0 } }
    const brief = { name: entry.name, created: entry.created, kind: 'scheduled', note: '', version: '0.1.0', uploaded: false, database_ok: true, files_ok: true, damaged: [], too_new: false, blocked: '', blocked_text: '', usable: true, facts: { events: 0, files: 0 }, details: { would_add: 0, would_change: 0, would_remove: 0, events_add: 3, events_change: 1, events_remove: 2, accounts_add: 0, accounts_remove: 1 } }
    serve((method, path, body) => (path === '/api/server/storage/backups' ? ok({ ...BACKUPS, count: 1, backups: [entry] }) : path.endsWith('/check') ? ok(brief) : server()(method, path, body)))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    await click(byText('Wiederherstellen', box.querySelector<HTMLElement>('[data-testid="backup-list"]')!))
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain('Termine: 3 kommen dazu, 1 ändern sich, 2 fallen weg.')
    expect(dialog.textContent).toContain('Konten: 0 kommen dazu, 1 fallen weg.')
    expect(calls.some((call) => call.path.endsWith('/restore'))).toBe(false)
  })
})

describe('TIME-1 AC-11 looking for new versions', () => {
  it('TIME-1 AC-11 the switch stands under "Über nextime" as in nexnotes, not under Server › Allgemein', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    const labels = [...box.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].map((item) => box.querySelector(`label[for="${item.id}"]`)?.textContent)
    expect(labels).not.toContain('Einmal am Tag nachsehen')
    expect(labels).not.toContain('Nach neuen Versionen suchen')
    // The order of General (plan "Einstellungen"): limits, the features for every account, interfaces, the languages.
    const cards = [...box.querySelectorAll('[data-testid="server-general"] [data-card]')].map((card) => card.getAttribute('data-card'))
    expect(cards).toEqual(['limits', 'features', 'interfaces', 'languages'])
  })
})

describe('TIME-1 EC-10 only the operator', () => {
  it('TIME-1 EC-10 a member does not see "Server" in the menu, and its address leads to "Mein Konto"', async () => {
    serve(signedIn(me()))
    for (const at of ['/server', '/settings/general', '/settings/storage']) {
      await render(<AuthProvider><App /></AuthProvider>, at)
      await settle()
      expect(box.querySelector('[data-testid^="server-"]')).toBeNull()
      expect(box.querySelector('[data-testid="settings-area-profile"]')).not.toBeNull()
      expect(box.querySelector('[data-testid="settings-nav-general"]')).toBeNull()
      expect(box.querySelector('[data-testid="settings-nav"]')!.textContent).not.toContain('Server')
    }
    expect(calls.some((call) => call.path === '/api/settings' || call.path === '/api/server/state')).toBe(false)
    // The sidebar leads to the settings only; their navigation has no group "Server" for a member.
    expect(box.querySelector('[data-testid="settings-entry"]')!.getAttribute('href')).toBe('/settings')
    expect(box.querySelector('[data-testid="settings-nav"]')!.textContent).toContain('Mein Konto')
    expect(box.querySelector('#settings-group-server')).toBeNull()
  })
})
