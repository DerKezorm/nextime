/** TIME-3 in the interface: the language, the section "Darstellung", week numbers and the first day in the views, the
 * hint while travelling, light or dark per device, a text missing in a language. */
import App from './App'
import { deviceZone } from './api/client'
import i18n, { firstShipped } from './i18n'
import { applyMode, storedMode } from './lib/theme'
import { AuthProvider } from './state/auth'
import { box, calls, choose, click, me, ok, render, serve, signedIn, start, stop } from './testing'

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

/** The server keeps the account's choices: a PUT of the display changes what /api/auth/me says afterwards. */
function account(start = me()) {
  let now = start
  return signedIn(now, (method, path, body) => {
    if (path === '/api/auth/me') return ok(now)
    if (path === '/api/me/display' && method === 'PUT') {
      now = { ...now, display: { ...now.display!, ...(body as object) } }
      return ok(now)
    }
    if (path === '/api/me/language' && method === 'PUT') {
      now = { ...now, language: (body as { language: string }).language }
      return ok(now)
    }
    return undefined
  })
}

describe('TIME-3 the language', () => {
  it('TIME-3 AC-9 a browser in German or English opens in its language, any other in English', () => {
    expect(firstShipped(['de-AT', 'en'])).toBe('de')
    expect(firstShipped(['en-GB'])).toBe('en')
    expect(firstShipped(['fr-FR', 'it'])).toBe('en')
  })

  it('TIME-3 AC-1 choosing a language changes the page at once and is kept for the account', async () => {
    serve(account())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    expect(box.textContent).toContain('Profilbild hochladen')
    await choose(box.querySelector<HTMLSelectElement>('[data-testid="language-select"]')!, 'en')
    expect(calls.find((call) => call.path === '/api/me/language')?.body).toEqual({ language: 'en' })
    expect(box.textContent).toContain('Upload a profile picture')
    expect(box.querySelector('h1')!.textContent).toBe('Profile')
    expect(i18n.language).toBe('en')
  })

  it('TIME-3 AC-1 a language of the operator shows with "vom Betreiber"', async () => {
    serve((method, path, body) => (path === '/api/locales' ? ok([{ code: 'es', name: 'Español', keys: 3 }]) : account()(method, path, body)))
    const { forgetAddedLanguages } = await import('./i18n')
    forgetAddedLanguages()
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    const options = [...box.querySelectorAll<HTMLOptionElement>('[data-testid="language-select"] option')].map((option) => option.textContent)
    expect(options).toContain('Español (vom Betreiber)')
    forgetAddedLanguages()
  })

  it('TIME-3 EC-2 a text missing in a language shows in English, never as its key', async () => {
    i18n.addResource('en', 'translation', 'probe.onlyEnglish', 'Only in English')
    await i18n.changeLanguage('de')
    expect(i18n.t('probe.onlyEnglish')).toBe('Only in English')
  })
})

describe('TIME-3 the section "Darstellung"', () => {
  it('TIME-3 AC-7 has light or dark, first day, week numbers and clock; zone and language are in the profile', async () => {
    serve(account())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/appearance')
    const area = box.querySelector<HTMLElement>('[data-testid="settings-area-appearance"]')!
    // "Aussehen" of the account module first (plan "Einstellungen"), then nextime's own cards.
    expect(area.querySelector('[data-card]')!.getAttribute('data-card')).toBe('appearance')
    for (const label of ['Hell oder dunkel', 'Erster Wochentag', 'Kalenderwochen', 'Uhrzeit']) expect(area.textContent).toContain(label)
    expect(area.querySelector('[data-testid="language-select"]')).toBeNull()
    expect(area.querySelector('#display-zone')).toBeNull()
    // Every day can begin the week (AC-2).
    expect(area.querySelector<HTMLSelectElement>('#display-week-start')!.options).toHaveLength(7)
    // As in nexnotes, the language travels with the account and stands in the profile, with its hint; the time zone
    // stands under it (E14), with the sentence on zones.
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    // The field of the shared account module (`PAGES.profile`, "Sprache") with nextime's hint joined to it.
    const select = box.querySelector<HTMLSelectElement>('#profile [data-testid="language-select"]')!
    expect(box.querySelector(`label[for="${select.id}"]`)!.textContent).toBe('Sprache')
    const hints = (select.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent)
    expect(hints).toContain(i18n.t('me.looks.languageHint'))
    const zone = box.querySelector<HTMLSelectElement>('#profile #display-zone')!
    expect(zone.options[0].textContent).toMatch(/^wie das Gerät \(.+\)$/)
    const zoneHints = (zone.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent)
    expect(zoneHints).toContain('Zeitzone folgt dem Gerät; auf Reisen zeigt nextime oben, in welcher Zeitzone die Zeiten stehen.')
  })

  it('TIME-3 AC-2 AC-3 AC-8 AC-6 each choice goes to the account at once', async () => {
    serve(account())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/appearance')
    await choose(box.querySelector<HTMLSelectElement>('#display-week-start')!, '0')
    await click(box.querySelector<HTMLInputElement>('[data-card="week-numbers"] input[type="checkbox"]')!)
    await click(box.querySelector<HTMLElement>('[data-testid="display-clock-12"]')!)
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    await choose(box.querySelector<HTMLSelectElement>('#display-zone')!, 'America/New_York')
    const sent = calls.filter((call) => call.path === '/api/me/display').map((call) => call.body)
    expect(sent).toEqual([{ week_start: 0 }, { week_numbers: false }, { clock: '12' }, { time_zone: 'America/New_York' }])
  })

  it('TIME-3 AC-4 light or dark belongs to the account on every device (konten 1.1.0, E5); "Wie das System" follows the device', async () => {
    const base = account()
    serve((method, path, body) => (path === '/api/me/theme' ? ok(me({ theme: 'light' })) : base(method, path, body)))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/appearance')
    const options = [...box.querySelectorAll<HTMLElement>('[data-card="appearance"] [role="radio"]')]
    expect(options.map((option) => option.textContent)).toEqual(['Hell', 'Dunkel', 'Wie das System'])
    await click(options[0])
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/me/theme')?.body).toEqual({ theme: 'light' })
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    applyMode('system')
  })

  it('TIME-3 AC-4 light, dark or like the device stays on this device', () => {
    applyMode('light')
    expect(storedMode()).toBe('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    applyMode('system')
    expect(storedMode()).toBe('system')
    // Nothing of it went to the server.
    expect(calls).toEqual([])
  })
})

describe('TIME-3 the views follow the choices', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-26T10:00:00Z'))
  })

  it('TIME-3 AC-2 AC-3 the week starts on Monday and shows "KW 48" beside the date', async () => {
    serve(account())
    await render(<AuthProvider><App /></AuthProvider>, '/week')
    const days = [...box.querySelectorAll('[data-testid="week-day"] div:first-child')].map((day) => day.textContent)
    expect(days[0]).toBe('Mo')
    expect(box.querySelector('[data-testid="week-number"]')!.textContent).toBe('KW 48')
  })

  it('TIME-3 AC-3 the month has a column of week numbers and today shows its number too', async () => {
    serve(account())
    await render(<AuthProvider><App /></AuthProvider>, '/month')
    expect(box.querySelector('[data-testid="week-column-head"]')!.textContent).toBe('KW')
    expect([...box.querySelectorAll('[data-testid="month-week-number"]')].map((cell) => cell.textContent)).toEqual(['44', '45', '46', '47', '48', '49'])
    await render(<AuthProvider><App /></AuthProvider>, '/')
    expect(box.querySelector('[data-testid="week-number"]')!.textContent).toBe('KW 48')
  })

  it('TIME-3 AC-3 switched off, week numbers stand nowhere', async () => {
    serve(account(me({ display: { week_start: 1, week_numbers: false, clock: 'auto', time_zone: '', home_zone: '' } })))
    for (const at of ['/', '/week', '/month']) {
      await render(<AuthProvider><App /></AuthProvider>, at)
      expect(box.querySelector('[data-testid="week-number"], [data-testid="month-week-number"], [data-testid="week-column-head"]')).toBeNull()
    }
  })

  it('TIME-3 EC-13 a week starting on Sunday or Saturday keeps the ISO number of its Thursday', async () => {
    serve(account(me({ display: { week_start: 0, week_numbers: true, clock: 'auto', time_zone: '', home_zone: '' } })))
    await render(<AuthProvider><App /></AuthProvider>, '/week')
    expect([...box.querySelectorAll('[data-testid="week-day"] div:first-child')][0].textContent).toBe('So')
    expect(box.querySelector('[data-testid="week-number"]')!.textContent).toBe('KW 48')
    serve(account(me({ display: { week_start: 6, week_numbers: true, clock: 'auto', time_zone: '', home_zone: '' } })))
    await render(<AuthProvider><App /></AuthProvider>, '/month')
    expect([...box.querySelectorAll('[data-testid="month-weekday"]')][0].textContent).toBe('Sa')
  })

  it('TIME-3 AC-5 AC-11 at home no hint; in another zone "Zeiten in New York (UTC−5)" on top', async () => {
    const home = (zone: string) => me({ display: { week_start: 1, week_numbers: true, clock: 'auto', time_zone: '', home_zone: zone } })
    serve(account(home(deviceZone())))
    await render(<AuthProvider><App /></AuthProvider>, '/')
    expect(box.querySelector('[data-testid="travel-hint"]')).toBeNull()
    serve(account(me({ display: { week_start: 1, week_numbers: true, clock: 'auto', time_zone: 'America/New_York', home_zone: 'Europe/Berlin' } })))
    await render(<AuthProvider><App /></AuthProvider>, '/')
    expect(box.querySelector('[data-testid="travel-hint"]')!.textContent).toBe('Zeiten in New York (UTC−5)')
  })

  it('TIME-3 EC-11 a device that changed its zone shows the new one at the next draw', async () => {
    const zone = vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions')
    zone.mockReturnValue({ ...new Intl.DateTimeFormat().resolvedOptions(), timeZone: 'Europe/Berlin' })
    serve(account(me({ display: { week_start: 1, week_numbers: true, clock: 'auto', time_zone: '', home_zone: 'Europe/Berlin' } })))
    await render(<AuthProvider><App /></AuthProvider>, '/')
    expect(box.querySelector('[data-testid="travel-hint"]')).toBeNull()
    zone.mockReturnValue({ ...new Intl.DateTimeFormat().resolvedOptions(), timeZone: 'Asia/Kolkata' })
    await render(<AuthProvider><App /></AuthProvider>, '/week')
    await click(box.querySelector('a[href="/"]'))
    expect(box.querySelector('[data-testid="travel-hint"]')!.textContent).toBe('Zeiten in Kolkata (UTC+5:30)')
  })
})

describe('TIME-3 the account\'s clock and zone count on "Server" and in the log too', () => {
  const OPERATOR = me({ role: 'operator', display: { week_start: 1, week_numbers: true, clock: '12', time_zone: 'America/New_York', home_zone: 'America/New_York' } })
  const SUMMARY = { schedule: 'daily', time: '03:00', zone: 'Europe/Berlin', keep: 14, kept: 1, count: 1, last: { created: '2026-11-22T08:00:00+00:00', size: 14 * 1024 * 1024 }, failure: null, running: false }
  const SETTINGS = {
    public_url: '', password_login: true, two_factor_required: false, backup_schedule: 'daily', backup_time: '03:00', backup_keep: 14,
    smtp_host: '', smtp_port: 587, smtp_security: 'starttls', smtp_user: '', smtp_password_set: false, smtp_from: '',
    upload_max_mb: 25, trash_days: 30, share_links: true, google_enabled: false, google_configured: false, api_enabled: true, connections_local_network: false,
    mcp_enabled: true, address_suggestions: true, update_check: true, data_total: 1000, data_used: 100, data_free: 900,
    nextime_used: 10, nextime_parts: { database: 10 }, data_on_share: false,
  }
  const LINE = { time: '2026-11-22 09:00:00', level: 'INFO', logger: 'nextime', message: 'probe line', request_id: null, user: null, at: '2026-11-22T08:00:00+00:00' }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-22T15:00:00Z'))
  })

  function server() {
    return signedIn(OPERATOR, (_method, path) => {
      if (path === '/api/backups/summary') return ok(SUMMARY)
      if (path === '/api/server/state') return ok({ version: '0.1.0', newer: null, https: true, internet: true, zone: 'Europe/Berlin' })
      if (path.startsWith('/api/logs/level')) return ok({ mode: 'normal', until: null, fixed_by_env: false, modes: ['quiet', 'normal', 'detailed', 'trace'] })
      if (path.startsWith('/api/logs')) return ok([LINE])
      if (path === '/api/settings') return ok(SETTINGS)
      if (path === '/api/oidc/authentik/address') return ok({ public_url: '', seen: 'http://localhost', stale: null })
      return undefined
    })
  }

  // TODO sicherung 1.0.2: the module's card writes its moments in the browser's zone and the language's clock; it has
  // no way yet to take the account's (question 1 of the settings pass). Until then this test waits.
  it.skip('TIME-3 AC-6 AC-8 12 hours fixed in German and a fixed zone: the last backup reads "heute 3:00 AM" in New York', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/storage')
    // 08:00 UTC is 03:00 in New York; the rhythm keeps the server's own time and zone.
    expect(box.querySelector('[data-testid="backup-line"]')!.textContent).toMatch(/^Täglich 03:00 \(Europe\/Berlin\) · zuletzt heute 3:00\sAM, 14 MB · 1 Stück aufbewahrt$/)
  })

  it('TIME-3 AC-6 AC-8 the log shows its times in the account\'s clock and zone', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/log')
    await vi.waitFor(() => expect(box.textContent).toContain('probe line'))
    expect(box.textContent).toMatch(/3:00:00\s?AM/)
    expect(box.textContent).not.toContain('09:00:00')
  })
})
