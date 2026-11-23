/** TIME-16 in the interface: the way "Google" in "Kalender hinzufügen" (connect, read only, or not there), the Google
 * row on "Kalender und Konten" with "Neu verbinden" and its hint, the way back from Google, and the operator's page
 * "Google einrichten" with five steps, the redirect address, the values and the warning before a change. */
import { act } from 'react'

import App from './App'
import { calendarList, calendar } from './calendarFakes'
import { browser } from './features/calendars/api'
import type { GoogleSetup, Sources } from './features/sources/api'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { googleSetup, row, sources } from './sourceFakes'
import { AuthProvider } from './state/auth'
import { box, calls, click, me, ok, render, serve, settle, signedIn, start, stop, type, type Answer } from './testing'
import { NOW, robin } from './viewFakes'

type More = (method: string, path: string, body: unknown) => Answer | undefined

const OPERATOR = me({ ...robin, role: 'operator' })
const SETTINGS = {
  public_url: '', password_login: true, two_factor_required: false, backup_schedule: 'daily', backup_time: '03:00', backup_keep: 14,
  smtp_host: '', smtp_port: 587, smtp_security: 'starttls', smtp_user: '', smtp_password_set: false, smtp_from: '',
  upload_max_mb: 25, trash_days: 30, share_links: true, google_enabled: false, google_configured: false, api_enabled: true, connections_local_network: false,
  mcp_enabled: true, address_suggestions: true, update_check: true, data_total: 1000, data_used: 100, data_free: 900,
  nextime_used: 10, nextime_parts: { database: 10 }, data_on_share: false,
}

function server(found: Sources, more: More = () => undefined, account = robin) {
  serve(
    signedIn(account, (method, path, body) => {
      const extra = more(method, path, body)
      if (extra) return extra
      if (path === '/api/sources' && method === 'GET') return ok(found)
      if (path.startsWith('/api/calendars')) return ok(calendarList([calendar(1, 'Familie', { default: true })]))
      if (path.startsWith('/api/collections')) return ok({ collections: [] })
      if (path.startsWith('/api/invitations')) return ok([])
      if (path === '/api/settings') return ok(SETTINGS)
      return undefined
    }),
  )
}

function operator(setup: GoogleSetup, more: More = () => undefined) {
  server(sources([]), (method, path, body) => more(method, path, body) ?? (path === '/api/server/google' && method === 'GET' ? ok(setup) : undefined), OPERATOR)
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  localStorage.clear()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

async function open(at = '/'): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const posted = (path: string) => calls.filter((call) => call.method !== 'GET' && call.path.startsWith(path))
const titleOf = (id: string) => one(id)!.closest('[role="dialog"]')!.getAttribute('aria-label')

describe('TIME-16 the way "Google"', () => {
  it('TIME-16 AC-2 set up: "Mit Google verbinden" says what Google will show, "Weiter zu Google" goes there', async () => {
    const went: string[] = []
    vi.spyOn(browser, 'go').mockImplementation((url) => void went.push(url))
    server(sources([], { google: { ready: true, configured: true } }), (method, path) => (path === '/api/sources/google/start' && method === 'POST' ? ok({ url: 'https://accounts.example.com/auth?x=1' }) : undefined))
    await open()
    await click(one('add-calendar'))
    expect(one('way-google')!.querySelector('[data-testid="tag-of-way"]')!.textContent).toBe('lesen und schreiben')
    await click(one('way-google'))
    expect(titleOf('google-dialog')).toBe('Mit Google verbinden')
    expect(one('google-unverified')!.textContent).toBe('Gleich zeigt Google „Diese App wurde nicht überprüft“. Bei selbst gehosteten Apps ist das normal: „Erweitert“ und dann „Weiter zu nextime“ wählen.')
    expect(one('google-dialog')!.textContent).toContain('nextime bekommt Zugriff nur auf deine Kalender, nicht auf Mail oder Dateien.')
    await click(one('google-go'))
    expect(posted('/api/sources/google/start')[0].body).toEqual({ source_id: null })
    expect(went).toEqual(['https://accounts.example.com/auth?x=1'])
  })

  it('TIME-16 AC-2 "Abbrechen" connects nothing', async () => {
    server(sources([], { google: { ready: true, configured: true } }))
    await open()
    await click(one('add-calendar'))
    await click(one('way-google'))
    await click([...one('google-dialog')!.querySelectorAll('button')].find((button) => button.textContent === 'Abbrechen'))
    expect(one('google-dialog')).toBeNull()
    expect(posted('/api/sources')).toEqual([])
  })

  it('TIME-16 AC-4 AC-9 not set up: "Google-Kalender nur lesen" with two steps, the secret address and the hour; "Hinzufügen" subscribes read only', async () => {
    let now = sources([])
    server(now, (method, path) => {
      if (path === '/api/sources/subscriptions' && method === 'POST') now = sources([row(5, { kind: 'subscription', google: true, label: 'calendar.google.com', calendars: [{ id: 51, name: 'Elternbeirat', color: 'red', read_only: true }] })])
      return path.startsWith('/api/sources') ? ok(now) : undefined
    })
    await open()
    await click(one('add-calendar'))
    expect(one('way-googleRead')!.querySelector('[data-testid="tag-of-way"]')!.textContent).toBe('nur lesen')
    await click(one('way-googleRead'))
    expect(titleOf('google-read-dialog')).toBe('Google-Kalender nur lesen')
    const dialog = one('google-read-dialog')!
    expect(dialog.textContent).toContain('Zum Lesen und Schreiben muss der Betreiber Google einmal einrichten.')
    expect([...one('google-read-steps')!.querySelectorAll('li')].map((step) => step.textContent)).toEqual(['Google Kalender im Browser öffnen, Kalender › Einstellungen', '„Privatadresse im iCal-Format“ kopieren'])
    expect(dialog.querySelector('label')!.textContent).toBe('Geheime Adresse')
    expect(one('hourly')!.textContent).toBe('nextime holt Änderungen einmal in der Stunde.')
    type(one('google-read-url') as HTMLInputElement, ' https://calendar.google.com/calendar/ical/x%40group/private-abc/basic.ics ')
    await click(one('google-read-go'))
    expect(posted('/api/sources/subscriptions')[0].body).toMatchObject({ url: 'https://calendar.google.com/calendar/ical/x%40group/private-abc/basic.ics', google: true, background: false })
    expect(one('google-read-dialog')).toBeNull()
  })

  it('TIME-16 EC-10 an address not from Google is refused at the field and nothing is added', async () => {
    server(sources([]), (method, path) =>
      path === '/api/sources/subscriptions' && method === 'POST' ? { status: 422, body: { detail: { code: 'not_google_address', message: 'No.' } } } : undefined,
    )
    await open()
    await click(one('add-calendar'))
    await click(one('way-googleRead'))
    type(one('google-read-url') as HTMLInputElement, 'https://example.com/x.ics')
    await click(one('google-read-go'))
    expect(one('google-read-url-problem')!.textContent).toBe('Das ist keine geheime Adresse von Google. Sie beginnt mit https://calendar.google.com/calendar/ical/.')
    expect(one('google-read-dialog')).not.toBeNull()
  })

  it('TIME-16 AC-5 set up and switched off: there is no way "Google"', async () => {
    server(sources([], { google: { ready: false, configured: true } }))
    await open()
    await click(one('add-calendar'))
    expect(one('way-google')).toBeNull()
    expect(one('way-googleRead')).toBeNull()
    expect(one('add-calendar-dialog')!.querySelectorAll('[data-testid^="way-"]')).toHaveLength(6)
  })

  it('TIME-16 EC-14 in English the read-only way names Google\'s menus in English', async () => {
    await i18n.changeLanguage('en')
    server(sources([]))
    await open()
    await click(one('add-calendar'))
    await click(one('way-googleRead'))
    expect(one('google-read-steps')!.textContent).toContain('Settings and sharing')
    expect(one('google-read-steps')!.textContent).toContain('“Secret address in iCal format”')
  })
})

describe('TIME-16 Google on "Kalender und Konten"', () => {
  const google = (change = {}) =>
    row(3, { kind: 'google', label: 'mara@example.org', calendars: [{ id: 31, name: 'Elternbeirat', color: 'red', read_only: false }], ...change })

  it('TIME-16 AC-8 EC-1 an expired Google access: the row in orange, the hint word for word, "Neu verbinden" goes through Google again', async () => {
    const went: string[] = []
    vi.spyOn(browser, 'go').mockImplementation((url) => void went.push(url))
    server(sources([google({ state: 'expired', since: '2026-11-25T17:02:00Z', synced_at: '2026-11-25T17:02:00Z', problem: 'credentials' })], { google: { ready: true, configured: true } }), (method, path) =>
      path === '/api/sources/google/start' && method === 'POST' ? ok({ url: 'https://accounts.example.com/again' }) : undefined,
    )
    await open('/settings/calendars')
    expect(one('source-kind')!.textContent).toBe('Google')
    expect(one('source-line')!.textContent).toBe('mara@example.org · Elternbeirat')
    expect(one('source-state')!.textContent).toBe('Zugang abgelaufen seit gestern 18:02')
    expect(one('expired-hint')!.textContent).toBe('Google-Zugang abgelaufen. „Elternbeirat“ zeigt weiter den Stand von gestern 18:02; Ändern geht erst nach dem neuen Verbinden.')
    await click(one('source-reconnect'))
    await click(one('google-go'))
    expect(posted('/api/sources/google/start')[0].body).toEqual({ source_id: 3 })
    expect(went).toEqual(['https://accounts.example.com/again'])
  })

  it('TIME-16 EC-5 switched off by the operator, the Google account rests', async () => {
    server(sources([google({ state: 'paused', problem: 'google_off' })]))
    await open('/settings/calendars')
    expect(one('source-state')!.textContent).toBe('ruht, solange Google ausgeschaltet ist')
    expect(one('source-reconnect')).toBeNull()
    expect(one('source-refresh')).toBeNull()
  })

  it('TIME-16 EC-6 back from Google without access: nothing is connected and a sentence says why', async () => {
    server(sources([]))
    await open('/settings/calendars?google=denied')
    expect(one('google-message')!.textContent).toBe('Ohne Zugriff auf die Kalender geht nichts. Es ist nichts verbunden.')
    expect(one('choose-dialog')).toBeNull()
  })

  it('TIME-16 AC-2 back from Google with an account: "Google verbunden" and the choice of its calendars', async () => {
    server(sources([google()]), (method, path) =>
      path === '/api/sources/3/calendars' && method === 'GET'
        ? ok({ source_id: 3, kind: 'google', label: 'mara@example.org', calendars: [{ url: 'elternbeirat', name: 'Elternbeirat', color: 'red', read_only: false, new: false, chosen: true }] })
        : undefined,
    )
    await open('/settings/calendars?choose=3')
    expect(titleOf('choose-dialog')).toBe('Google verbunden')
    expect(one('choose-name')!.textContent).toBe('Elternbeirat')
  })

  it('TIME-16 EC-7 the same Google account again: no second connection, the choice of the one there', async () => {
    server(sources([google()]), (method, path) =>
      path === '/api/sources/3/calendars' && method === 'GET' ? ok({ source_id: 3, kind: 'google', label: 'mara@example.org', calendars: [] }) : undefined,
    )
    await open('/settings/calendars?google=exists&choose=3')
    expect(titleOf('choose-dialog')).toBe('Google-Kalender wählen')
    expect(one('choose-note')!.textContent).toBe('Dieses Konto ist schon verbunden. Hier wählst du seine Kalender.')
  })

  it('TIME-16 EC-11 a Google address reset in Google: the last events stay, a new address can be entered', async () => {
    const read = row(5, { kind: 'subscription', google: true, label: 'calendar.google.com', state: 'expired', problem: 'subscription_gone', synced_at: '2026-11-23T08:00:00Z', calendars: [{ id: 51, name: 'Elternbeirat', color: 'red', read_only: true }] })
    server(sources([read]), (method, path) => (path === '/api/sources/5/link' && method === 'PUT' ? ok(sources([{ ...read, state: 'ok' }])) : undefined))
    await open('/settings/calendars')
    expect(one('source-kind')!.textContent).toBe('Google (nur lesen)')
    expect(one('source-state')!.textContent).toMatch(/^seit .+ nicht aktualisiert, die letzten Termine bleiben$/)
    await click(one('source-link'))
    type(one('link-url') as HTMLInputElement, 'https://calendar.google.com/calendar/ical/x/private-new/basic.ics')
    await click(one('link-go'))
    expect(posted('/api/sources/5/link')[0].body).toEqual({ url: 'https://calendar.google.com/calendar/ical/x/private-new/basic.ics' })
  })
})

describe('TIME-16 "Google einrichten" for the operator', () => {
  it('TIME-16 AC-1 AC-6 five steps, the redirect address with "Kopieren", client ID and secret, the secret never filled in', async () => {
    Object.defineProperty(document, 'execCommand', { value: () => true, configurable: true })
    operator(googleSetup({ client_id: '2741.apps.googleusercontent.com', secret_set: true }))
    await open('/settings/google')
    const steps = all('google-step')
    expect(steps).toHaveLength(5)
    expect(steps.map((step) => step.querySelector('strong')!.textContent)).toEqual(['Projekt anlegen', 'Google Calendar API einschalten', 'Zustimmungsseite einrichten', 'Zugangsdaten erstellen', 'Werte hier eintragen'])
    expect(steps[2].textContent).toContain('Extern, App-Name nextime, Status „In Produktion“, damit Freigaben nicht nach 7 Tagen ablaufen.')
    expect(steps.every((step) => step.dataset.done === 'false')).toBe(true)
    expect(one('google-redirect')!.textContent).toBe('https://nextime.example.com/api/google/callback')
    await click(one('google-copy'))
    expect(one('google-copy')!.textContent).toBe('Kopiert')
    expect((one('google-client-id') as HTMLInputElement).value).toBe('2741.apps.googleusercontent.com')
    const secret = one('google-secret') as HTMLInputElement
    expect(secret.type).toBe('password')
    expect(secret.value).toBe('')
    expect(secret.placeholder).toBe('gespeichert; zum Ändern neu eingeben')
  })

  it('TIME-16 AC-7 AC-1 once the values fit: "Google bereit. 3 Mitglieder sind verbunden." and every step ticked', async () => {
    operator(googleSetup(), (method, path) =>
      path === '/api/server/google' && method === 'PUT' ? ok(googleSetup({ client_id: 'id.apps.googleusercontent.com', secret_set: true, ready: true, enabled: true, members: 3 })) : undefined,
    )
    await open('/settings/google')
    expect(one('google-ready')).toBeNull()
    type(one('google-client-id') as HTMLInputElement, ' id.apps.googleusercontent.com ')
    type(one('google-secret') as HTMLInputElement, ' geheim ')
    await click(one('google-save'))
    expect(posted('/api/server/google')[0].body).toEqual({ client_id: 'id.apps.googleusercontent.com', client_secret: 'geheim', confirm: false })
    expect(one('google-ready')!.textContent).toBe('Google bereit. 3 Mitglieder sind verbunden.')
    expect(all('google-step').every((step) => step.dataset.done === 'true')).toBe(true)
    expect((one('google-secret') as HTMLInputElement).value).toBe('')
  })

  it('TIME-16 EC-3 a value that does not fit is named at its field, and "Google bereit" does not show', async () => {
    operator(googleSetup(), (method, path) =>
      path === '/api/server/google' && method === 'PUT' ? { status: 422, body: { detail: { code: 'google_client_id_wrong', message: 'No.', field: 'client_id' } } } : undefined,
    )
    await open('/settings/google')
    type(one('google-client-id') as HTMLInputElement, 'falsch')
    type(one('google-secret') as HTMLInputElement, 'geheim')
    await click(one('google-save'))
    expect(one('google-client-id-problem')!.textContent).toBe('Google kennt diese Client-ID nicht.')
    expect(one('google-ready')).toBeNull()
  })

  it('TIME-16 EC-2 changing the values with members connected warns first; only "Trotzdem ändern" sends with confirm', async () => {
    let asked = 0
    operator(googleSetup({ client_id: 'old.apps.googleusercontent.com', secret_set: true, ready: true, enabled: true, members: 2 }), (method, path, body) => {
      if (path !== '/api/server/google' || method !== 'PUT') return undefined
      asked += 1
      if (!(body as { confirm: boolean }).confirm) return { status: 409, body: { detail: { code: 'google_change_needs_confirm', message: 'No.', count: 2 } } }
      return ok(googleSetup({ client_id: 'new.apps.googleusercontent.com', secret_set: true, ready: true, enabled: true, members: 0 }))
    })
    await open('/settings/google')
    expect(one('google-warning')!.textContent).toBe('Werden die Werte geändert, müssen alle Mitglieder Google neu verbinden.')
    type(one('google-client-id') as HTMLInputElement, 'new.apps.googleusercontent.com')
    type(one('google-secret') as HTMLInputElement, 'neu')
    await click(one('google-save'))
    expect(one('google-confirm')!.textContent).toContain('Werden die Werte geändert, müssen alle Mitglieder Google neu verbinden.')
    expect(asked).toBe(1)
    await click(one('google-confirm-go'))
    expect(posted('/api/server/google').map((call) => (call.body as { confirm: boolean }).confirm)).toEqual([false, true])
    expect(one('google-confirm')).toBeNull()
  })

  it('TIME-16 EC-2 removing the set-up with members connected asks first', async () => {
    operator(googleSetup({ client_id: 'old.apps.googleusercontent.com', secret_set: true, ready: true, enabled: true, members: 2 }), (method, path) =>
      path.startsWith('/api/server/google') && method === 'DELETE' ? ok(googleSetup()) : undefined,
    )
    await open('/settings/google')
    await click(one('google-remove'))
    expect(one('google-confirm')).not.toBeNull()
    expect(calls.filter((call) => call.method === 'DELETE')).toEqual([])
    await click(one('google-confirm-go'))
    expect(calls.filter((call) => call.method === 'DELETE').map((call) => call.path)).toEqual(['/api/server/google?confirm=true'])
  })

  it('TIME-16 EC-4 EC-13 without HTTPS and after the address moved the page says so', async () => {
    operator(googleSetup({ https: false, redirect_changed: true, redirect_uri: 'http://nextime.local/api/google/callback' }))
    await open('/settings/google')
    expect(one('google-https')!.textContent).toBe('nextime ist nicht über HTTPS erreichbar. So verbindet Google nicht; richte zuerst HTTPS ein.')
    expect(one('google-moved')!.textContent).toBe('Die Adresse von nextime hat sich geändert. Trage die Weiterleitungsadresse bei Google neu ein.')
    expect(one('google-redirect')!.textContent).toBe('http://nextime.local/api/google/callback')
  })

  it('TIME-16 EC-14 in English the steps name Google\'s menus in English', async () => {
    await i18n.changeLanguage('en')
    operator(googleSetup())
    await open('/settings/google')
    const text = one('google-steps')!.textContent
    expect(text).toContain('“APIs & Services”')
    expect(text).toContain('“In production”')
    expect(text).toContain('“Web application”')
  })

  it('TIME-16 AC-1 a member does not reach "Google einrichten"', async () => {
    server(sources([]))
    await open('/settings/google')
    expect(one('google-area')).toBeNull()
    expect(calls.some((call) => call.path === '/api/server/google')).toBe(false)
    await act(async () => undefined)
    await settle()
    expect(box.querySelector('[data-testid="settings-area-profile"]')).not.toBeNull()
  })
})
