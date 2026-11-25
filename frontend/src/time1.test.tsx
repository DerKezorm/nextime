/** TIME-1 in the interface: the setup page, the HTTPS hint for the operator, and the settings' group "Server" with its
 * switches, limits and state under General and the backups under Storage and backups; a member never reaches it. */
import { Route, Routes } from 'react-router-dom'

import App from './App'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, ok, render, serve, settle, signedIn, start, stop, type } from './testing'
import { wholeNumber } from './pages/settings/ServerCards'
import { backupLine } from './pages/settings/ServerCards'
import type { BackupSummary, ServerState } from './api/client'

const OPERATOR = me({ id: 1, name: 'tester', display_name: 'Tester', role: 'operator' })

const SETTINGS = {
  public_url: '', password_login: true, two_factor_required: false, backup_schedule: 'daily', backup_time: '03:00', backup_keep: 14,
  smtp_host: '', smtp_port: 587, smtp_security: 'starttls', smtp_user: '', smtp_password_set: false, smtp_from: '',
  upload_max_mb: 25, trash_days: 30, share_links: true, google_enabled: false, google_configured: false, api_enabled: true, connections_local_network: false,
  mcp_enabled: true, address_suggestions: true, update_check: true, data_total: 1000, data_used: 100, data_free: 900,
  nextime_used: 10, nextime_parts: { database: 10 }, data_on_share: false,
}
const SUMMARY: BackupSummary = { schedule: 'daily', time: '03:00', zone: 'Europe/Berlin', keep: 14, kept: 14, count: 14, last: null, failure: null, running: false }
const STATE: ServerState = { version: '0.1.0', newer: '0.2.0', https: true, internet: true, zone: 'Europe/Berlin' }

function server(state = STATE, settings = SETTINGS, summary = SUMMARY) {
  return signedIn(OPERATOR, (method, path, body) => {
    if (path === '/api/settings' && method === 'GET') return ok(settings)
    if (path === '/api/settings' && method === 'PUT') return ok({ ...settings, ...(body as object) })
    if (path === '/api/server/state') return ok(state)
    if (path === '/api/backups/summary') return ok(summary)
    if (path === '/api/backups') return ok([])
    if (path.startsWith('/api/oidc/admin/providers')) return ok([])
    if (path === '/api/oidc/authentik/address') return ok({ public_url: '', seen: 'http://localhost', stale: null })
    return undefined
  })
}

beforeEach(async () => {
  start()
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
  it('TIME-1 AC-7 shows the running version and "Neue Version" beside it', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('[data-testid="server-version"]')!.textContent).toBe('Version 0.1.0')
    expect(box.querySelector('[data-testid="server-newer"]')!.textContent).toBe('Neue Version 0.2.0')
  })

  it('TIME-1 EC-13 without an answer about a newer version only the hint is missing', async () => {
    serve(server({ ...STATE, newer: null }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('[data-testid="server-newer"]')).toBeNull()
    expect(box.querySelector('[role="alert"]')).toBeNull()
  })

  it('TIME-1 AC-5 has the switches; Google says whether it is set up and cannot be switched before', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('#switches h2')!.textContent).toBe('Funktionen für alle Konten')
    const switches = [...box.querySelectorAll<HTMLInputElement>('[data-testid="switches"] input[type="checkbox"]')]
    // "Verbindungen ins eigene Netz" for nextasks (TIME-25), off from the start, as nexnotes.
    expect(switches.map((item) => item.getAttribute('aria-label'))).toEqual(['Teilen per Link', 'MCP-Zugang', 'Programmschnittstelle (API) für Mitglieder', 'Verbindungen ins eigene Netz', 'Google-Anbindung', 'Adressvorschläge'])
    expect(switches.map((item) => item.checked)).toEqual([true, true, true, false, false, true])
    expect(switches[4].disabled).toBe(true)
    expect(box.textContent).toContain('(nicht eingerichtet)')
    // A click on the row switches it, as in nexnotes: the whole row is the label.
    await click(switches[5].closest('label')!)
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/settings')?.body).toEqual({ address_suggestions: false })
  })

  it('TIME-1 AC-9 the state shows HTTPS and the internet, green when right, a warning otherwise; "Log ansehen" leads to the log', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('[data-testid="state-https"]')!.textContent).toBe('HTTPS aktiv, iPhones können sich verbinden')
    expect(box.querySelector('[data-testid="state-internet"]')!.textContent).toBe('Internet erreichbar, Abgleich läuft')
    expect(box.querySelector('[data-testid="https-hint"]')).toBeNull()
    expect([...box.querySelectorAll('a')].find((link) => link.textContent === 'Log ansehen')!.getAttribute('href')).toBe('/settings/log')
  })

  it('TIME-1 AC-6 AC-9 without HTTPS and internet the operator sees the hint and both warnings', async () => {
    serve(server({ ...STATE, https: false, internet: false }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    expect(box.querySelector('[data-testid="https-hint"]')!.textContent).toBe('nextime ist nicht über HTTPS erreichbar. Kalender auf iPhones und Android verbinden sich erst mit HTTPS.')
    expect(box.querySelector('[data-testid="state-https"]')!.getAttribute('data-ok')).toBe('false')
    expect(box.querySelector('[data-testid="state-internet"]')!.getAttribute('data-ok')).toBe('false')
  })

  it('TIME-1 AC-4 EC-11 a wrong limit is said at the field, nothing is sent, the old value stays', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/general')
    const form = box.querySelector('[data-testid="limit-trash"]')!
    const field = form.querySelector('input')!
    expect(field.value).toBe('30')
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

  it('TIME-1 AC-8 the box says rhythm with zone, last backup with time and size, how many are kept', () => {
    const t = i18n.getFixedT('de')
    const now = new Date('2026-11-22T10:00:00')
    const line = backupLine({ ...SUMMARY, last: { created: new Date('2026-11-22T03:00:00').toISOString(), size: 14 * 1024 * 1024 } }, t, 'de', now)
    expect(line).toBe('Täglich 03:00 (Europe/Berlin) · zuletzt heute 03:00, 14 MB · 14 Stück aufbewahrt')
    expect(backupLine({ ...SUMMARY, kept: 1 }, i18n.getFixedT('en'), 'en', now)).toBe('Daily 03:00 (Europe/Berlin) · No backups yet. · 1 kept')
  })

  it('TIME-1 EC-15 the button shows that a backup is running and does not start a second one', async () => {
    serve(server(STATE, SETTINGS, { ...SUMMARY, running: true }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    const button = byText('Sicherung läuft …', box) as HTMLButtonElement
    expect(button).toBeDefined()
    expect(button.disabled).toBe(true)
  })

  it('TIME-1 EC-7 a failed backup is shown with its reason', async () => {
    serve(server(STATE, SETTINGS, { ...SUMMARY, failure: { at: '2026-11-22T02:00:00Z', reason: 'disk_full' } }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    expect(box.querySelector('[data-testid="backup-failure"]')!.textContent).toContain('Der Datenträger ist voll')
  })

  it('TIME-1 AC-8 "Einspielen …" checks first and shows what comes, changes and goes before asking', async () => {
    const entry = { name: 'nextime-2026-11-22-030000.zip', size: 1000, created: '2026-11-22T03:00:00+00:00', kind: 'scheduled', note: '', events: 0, files: 0, version: '0.1.0', uploaded: false }
    const brief = { name: entry.name, version: '0.1.0', created: entry.created, kind: 'scheduled', events: 0, files: 0, database_ok: true, files_ok: true, damaged: [], would_add: 0, would_change: 0, would_remove: 0, events_add: 3, events_change: 1, events_remove: 2, accounts_add: 0, accounts_remove: 1, usable: true }
    serve((method, path, body) => (path === '/api/backups' ? ok([entry]) : path.endsWith('/check') ? ok(brief) : server()(method, path, body)))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    await click(byText('Einspielen …', box))
    await click(byText('Wiederherstellen', box))
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
    const labels = [...box.querySelectorAll('input[type="checkbox"]')].map((item) => item.getAttribute('aria-label'))
    expect(labels).not.toContain('Einmal am Tag nachsehen')
    expect(labels).not.toContain('Nach neuen Versionen suchen')
    // The order of General as in nexnotes: limits, the features for every account, the languages.
    const cards = [...box.querySelectorAll('[data-testid="server-general"] section[id]')].map((card) => card.id)
    expect(cards.slice(0, 3)).toEqual(['limits', 'switches', 'languages'])
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
