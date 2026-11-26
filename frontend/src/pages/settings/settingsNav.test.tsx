/**
 * TIME-2 AC-7, the settings in areas (plan "Einstellungen", in the frame of the ui kit): the navigation with the groups
 * "Mein Konto" and "Server", each area at its own address, `/settings` as the profile (on a phone the list), the
 * addresses of before (`/account/<tab>`, `/server/<page>`, `/settings/reminders`) leading to their area with the query
 * kept (the answer of a provider after linking lands in Security), a member sent from a server area to the profile, the
 * current area marked with `aria-current`, the way back on a phone, every card in one area, and "Über nextime".
 */
import { useLocation } from 'react-router-dom'

import App from '../../App'
import type { Me } from '../../api/client'
import i18n from '../../i18n'
import { AuthProvider } from '../../state/auth'
import { box, byText, calls, click, KEYS_LISTING, me, ok, render, serve, signedIn, start, stop } from '../../testing'
import { formerPath, resolveArea } from './areas'

const OPERATOR = me({ id: 1, name: 'tester', display_name: 'Tester', role: 'operator' })

const SETTINGS = {
  public_url: '', password_login: true, two_factor_required: false, backup_schedule: 'daily', backup_time: '03:00', backup_keep: 14,
  smtp_host: '', smtp_port: 587, smtp_security: 'starttls', smtp_user: '', smtp_password_set: false, smtp_from: '',
  upload_max_mb: 25, trash_days: 30, share_links: true, google_enabled: false, google_configured: false, api_enabled: true,
  mcp_enabled: true, address_suggestions: true, update_check: true, data_total: 1000, data_used: 100, data_free: 900,
  nextime_used: 10, nextime_parts: { database: 10 }, data_on_share: false,
}
const ABOUT = { version: '0.1.0', license: 'AGPL-3.0', repo_url: 'https://example.com/nextime', releases_url: 'https://example.com/nextime/releases', project_url: 'https://example.com/nextime' }
const UPDATES = { update_check: true, checked: true, latest: 'v0.2.0', newer: true, checked_at: '2026-11-22T08:00:00Z', release_url: 'https://example.com/nextime/releases/tag/v0.2.0' }

const SIGN_IN = { password_login: true, factor_required: false, passkeys_allowed: true, password_may_go_off: false, factor_may_go_on: false, public_url: '', operator_has_password: true }
const MAIL = { host: '', port: 587, security: 'starttls', user: '', password_set: false, sender: '', sender_name: '', sender_name_default: 'nextime', configured: false, last_ok_at: null, last_error: '', last_error_at: null, waiting: 0 }
const STORAGE = { app: 'nextime', used: 10, parts: [{ key: 'database', bytes: 10, within: '', complete: true }], complete: true, total: 1000, disk_used: 100, free: 900, nearly_full: false, where: {}, network: [] }
const BACKUPS = { schedule: { every: 'daily', time: '03:00', keep: 14 }, zone: 'Europe/Berlin', last: null, count: 0, kept: 0, failure: null, running: false, restore_failure: null, restored: null, restore: 'restart', download_password: false, backups: [] }

const REMINDERS = {
  not_double: true,
  apple: [],
  this_browser: { subscribed: false, reminders: true, quiet: false },
  browsers: [],
  calendars: [{ id: 1, name: 'Familie', color: 'green', owner: null, kind: 'own', hidden: false, choice: 'none', options: ['none', '5', '15', '30', '60', '1440'] }],
  delivery: { https: true, internet: true },
  preview: { title: 'Zahnarzt Ben · in 15 Minuten', text: '16:30–17:15 · Praxis Dr. Weber, Lindenstraße 4', snooze: 'In 5 Min erneut', open: 'Öffnen' },
}

function server(account: Me = OPERATOR) {
  return signedIn(account, (_method, path) => {
    if (path === '/api/settings') return ok(SETTINGS)
    if (path === '/api/server/state') return ok({ version: '0.1.0', newer: null, https: true, internet: true, zone: 'Europe/Berlin' })
    if (path === '/api/backups/summary') return ok({ schedule: 'daily', time: '03:00', zone: 'Europe/Berlin', keep: 14, kept: 0, count: 0, last: null, failure: null, running: false })
    if (path === '/api/oidc/authentik/address') return ok({ public_url: '', seen: 'http://localhost', stale: null })
    if (path.startsWith('/api/logs/level')) return ok({ mode: 'normal', until: null, fixed_by_env: false, modes: ['quiet', 'normal', 'detailed', 'trace'] })
    if (path === '/api/oidc/me') return ok([{ slug: 'entra', label: 'Microsoft', linked: true, managed: '' }])
    if (path === '/api/about') return ok(ABOUT)
    if (path === '/api/about/updates') return ok(UPDATES)
    if (path === '/api/devices') return ok({ devices: [], https: true, address: 'https://nextime.example.com/dav/', user: 'tester', calendars: [], minutes: 10 })
    if (path === '/api/reminders') return ok(REMINDERS)
    if (path === '/api/notify/webpush') return ok({ key: 'k', subscribed: false, reminders: true })
    if (path === '/api/app/devices') return ok([])
    if (path === '/api/connections/keys') return ok(KEYS_LISTING)
    if (path === '/api/connections/interfaces') return ok({ api: true, mcp: true })
    if (path === '/api/connections/admin/keys') return ok({ keys: [] })
    if (path === '/api/accounts') return ok([])
    if (path === '/api/invites') return ok([])
    if (path === '/api/server/sign-in') return ok(SIGN_IN)
    if (path === '/api/server/sign-in/readiness') return ok({ state: 'ok', open: 0, points: [] })
    if (path === '/api/oidc/admin/providers') return ok([])
    if (path === '/api/server/mail') return ok(MAIL)
    if (path === '/api/server/storage') return ok(STORAGE)
    if (path === '/api/server/storage/backups') return ok(BACKUPS)
    if (path === '/api/locales') return ok([])
    return undefined
  })
}

function Where() {
  const location = useLocation()
  return <output data-testid="where">{location.pathname + location.search}</output>
}

async function open(at: string): Promise<void> {
  await render(
    <AuthProvider>
      <App />
      <Where />
    </AuthProvider>,
    at,
  )
}

const where = () => box.querySelector('[data-testid="where"]')!.textContent
const $ = (testId: string) => box.querySelector<HTMLElement>(`[data-testid="${testId}"]`)
const current = () => [...box.querySelectorAll('[data-testid="settings-nav"] [aria-current="page"]')].map((entry) => entry.getAttribute('data-testid'))
const heading = () => box.querySelector('h1')!.textContent
const cards = () => [...box.querySelectorAll('[data-part="settings-main"] [data-card]')].filter((card) => !card.parentElement?.closest('[data-card]')).map((card) => card.getAttribute('data-card'))

const ACCOUNT_AREAS = ['profile', 'security', 'appearance', 'calendars', 'shares', 'devices', 'move', 'notifications', 'connections']
const SERVER_AREAS = ['general', 'sign-in', 'mail', 'storage', 'google', 'accounts', 'log']

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(stop)

describe('the addresses of the settings (TIME-2 AC-7)', () => {
  it('TIME-2 AC-7 /settings is the profile, an unknown area leads there, "Über nextime" is everyone\'s', () => {
    expect(resolveArea(undefined, 'member', '')).toEqual({ area: 'profile' })
    for (const slug of [...ACCOUNT_AREAS, ...SERVER_AREAS, 'about']) expect(resolveArea(slug, 'operator', '')).toEqual({ area: slug })
    for (const slug of [...ACCOUNT_AREAS, 'about']) expect(resolveArea(slug, 'member', '')).toEqual({ area: slug })
    expect(resolveArea('nothing-here', 'operator', '')).toEqual({ redirect: '/settings' })
  })

  it('TIME-1 EC-10 a member asking for a server area lands on the profile', () => {
    for (const slug of SERVER_AREAS) expect(resolveArea(slug, 'member', '')).toEqual({ redirect: '/settings' })
  })

  it('TIME-2 AC-7 the answer of a provider after linking goes to Security, with its message', () => {
    expect(resolveArea(undefined, 'member', '?linked=entra')).toEqual({ redirect: '/settings/security?linked=entra' })
    expect(resolveArea(undefined, 'member', '?error=oidc_failed')).toEqual({ redirect: '/settings/security?error=oidc_failed' })
  })

  it('TIME-2 AC-7 every address of before leads to its area, with the query kept', () => {
    expect(formerPath('/account', '')).toBe('/settings/profile')
    expect(formerPath('/account/', '')).toBe('/settings/profile')
    expect(formerPath('/account/general', '')).toBe('/settings/profile')
    for (const tab of ['calendars', 'shares', 'devices', 'connections']) expect(formerPath(`/account/${tab}`, '')).toBe(`/settings/${tab}`)
    // "Erinnerungen" is "Benachrichtigungen" now (plan "Einstellungen", E1).
    expect(formerPath('/account/reminders', '')).toBe('/settings/notifications')
    expect(resolveArea('reminders', 'member', '?x=1')).toEqual({ redirect: '/settings/notifications?x=1' })
    expect(formerPath('/server', '')).toBe('/settings/general')
    for (const page of ['accounts', 'google', 'log']) expect(formerPath(`/server/${page}`, '')).toBe(`/settings/${page}`)
    expect(formerPath('/server/accounts', '?invite=1')).toBe('/settings/accounts?invite=1')
    expect(formerPath('/account/nothing', '')).toBe('/settings')
    expect(formerPath('/server/nothing', '')).toBe('/settings')
    // Where the server sends the browser back after linking a provider (`ACCOUNT_PAGE` in oidc_store.py).
    expect(formerPath('/account', '?linked=entra')).toBe('/settings?linked=entra')
    expect(formerPath('/account', '?error=oidc_failed')).toBe('/settings?error=oidc_failed')
  })

  it.each([
    ['/account', '/settings/profile', 'profile'],
    ['/account/general', '/settings/profile', 'profile'],
    ['/account/calendars', '/settings/calendars', 'calendars'],
    ['/account/shares', '/settings/shares', 'shares'],
    ['/account/devices', '/settings/devices', 'devices'],
    ['/account/connections', '/settings/connections', 'connections'],
    ['/account/reminders', '/settings/notifications', 'notifications'],
    ['/settings/reminders', '/settings/notifications', 'notifications'],
    ['/server', '/settings/general', 'general'],
    ['/server/accounts', '/settings/accounts', 'accounts'],
    ['/server/google', '/settings/google', 'google'],
    ['/server/log', '/settings/log', 'log'],
  ])('TIME-2 AC-7 %s of before opens %s, with the address replaced and the area marked', async (before, after, slug) => {
    serve(server())
    await open(before)
    expect(where()).toBe(after)
    expect($(`settings-area-${slug}`)).not.toBeNull()
    expect(current()).toEqual([`settings-nav-${slug}`])
  })

  it('TIME-2 AC-7 back from linking at /account, the message of the provider shows in Security', async () => {
    serve(server(me()))
    await open('/account?linked=entra')
    expect(where()).toBe('/settings/security?linked=entra')
    expect(current()).toEqual(['settings-nav-security'])
    expect(box.textContent).toContain(i18n.t('me.linked', { name: 'Microsoft' }))
  })

  it('TIME-2 AC-7 a refusal of a provider at /account shows in Security too', async () => {
    serve(server(me()))
    await open('/account?error=oidc_failed')
    expect(where()).toBe('/settings/security?error=oidc_failed')
    expect($('settings-area-security')).not.toBeNull()
    expect(box.querySelector('[role="alert"]')).not.toBeNull()
  })

  it('TIME-1 EC-10 a member at a server address of before or now sees the profile, marked as such', async () => {
    for (const at of ['/server/accounts', '/settings/accounts', '/settings/google']) {
      serve(server(me()))
      await open(at)
      expect(where()).toBe('/settings')
      expect(heading()).toBe('Profil')
      expect(current()).toEqual(['settings-nav-profile'])
      expect($('settings-nav-accounts')).toBeNull()
      expect($('settings-nav')!.textContent).not.toContain('Server')
    }
  })

  it('TIME-2 AC-7 an account that still has to set up its second factor goes to Security, and keeps a provider\'s answer', async () => {
    serve(server(me({ second_factor_setup_required: true })))
    await open('/month')
    expect(where()).toBe('/settings/security')
    expect(box.textContent).toContain(i18n.t('me.setupFirst'))
    await open('/account?linked=entra')
    expect(where()).toBe('/settings/security?linked=entra')
    await open('/settings/appearance')
    expect(where()).toBe('/settings/appearance')
  })
})

describe('the navigation of the settings (TIME-2 AC-7)', () => {
  it('TIME-2 AC-7 the operator sees both groups, the account with name and role at the top, "Über nextime" last', async () => {
    serve(server())
    await open('/settings')
    const nav = $('settings-nav')!
    expect(nav.tagName).toBe('NAV')
    expect(nav.textContent).toContain('Tester')
    expect(nav.textContent).toContain('Betreiber')
    const groups = [...nav.querySelectorAll('ul')].map((list) => box.querySelector(`#${list.getAttribute('aria-labelledby')}`)!.textContent)
    expect(groups).toEqual(['Mein Konto', 'Server'])
    expect([...nav.querySelectorAll('ul')][0].textContent).toBe('ProfilSicherheitDarstellungKalender und KontenFreigabenAuf iPhone und CoMitnehmen und umziehenBenachrichtigungenVerbindungen')
    expect([...nav.querySelectorAll('ul')][1].textContent).toBe('AllgemeinAnmeldungMailSpeicher und SicherungGoogle einrichtenKontenLog')
    const entries = [...nav.querySelectorAll('a')]
    expect(entries.at(-1)!.getAttribute('href')).toBe('/settings/about')
    expect(entries.at(-1)!.textContent).toBe('Über nextime')
  })

  it('TIME-2 AC-7 every entry is a link at its own address, reached by Tab; only the current one is marked', async () => {
    serve(server())
    await open('/settings/sign-in')
    const links = [...box.querySelectorAll<HTMLAnchorElement>('[data-testid="settings-nav"] a[data-testid^="settings-nav-"]')]
    expect(links.map((link) => link.getAttribute('href'))).toEqual([...ACCOUNT_AREAS, ...SERVER_AREAS, 'about'].map((slug) => `/settings/${slug}`))
    for (const link of links) expect(link.tabIndex).toBe(0)
    expect(current()).toEqual(['settings-nav-sign-in'])
    expect($('settings-nav-sign-in')!.className).toContain('--nexui-accent')
    expect($('settings-nav-mail')!.className).not.toContain('--nexui-accent)_14%')
    await click($('settings-nav-mail'))
    expect(where()).toBe('/settings/mail')
    expect(current()).toEqual(['settings-nav-mail'])
    expect(heading()).toBe('Mail')
    expect($('settings-area-mail')!.textContent).toContain('Der Mailserver, über den nextime Einladungen, Links und Benachrichtigungen verschickt.')
  })

  it('TIME-2 AC-7 the sidebar ends with picture, name and the gear, named "<name> · Einstellungen", marked only in the settings', async () => {
    serve(server())
    await open('/week')
    const entry = $('settings-entry')!
    expect(entry.getAttribute('href')).toBe('/settings')
    expect(entry.textContent).toBe('TTester') // the initial, then the name; the gear says the rest
    expect(entry.getAttribute('aria-label')).toBe('Tester · Einstellungen')
    expect(entry.querySelector('[data-testid="avatar-initial"], img')).not.toBeNull()
    expect(entry.querySelector('svg.lucide-settings')).not.toBeNull()
    expect(entry.className).not.toContain('nt-chosen') // as in nexnotes: on the wash only while the settings are open
    expect(entry.getAttribute('aria-current')).toBeNull()
    // The account menu of before is gone: signing out is in the profile, as in nexnotes.
    expect($('account-button')).toBeNull()
    for (const at of ['/settings', '/settings/log', '/settings/about']) {
      await open(at)
      expect($('settings-entry')!.getAttribute('aria-current')).toBe('page')
      expect($('settings-entry')!.className).toContain('nt-chosen')
    }
  })

  it('TIME-2 AC-7 the navigation has the account on top, groups in capitals, a symbol per entry, "Über nextime" below a line', async () => {
    serve(server())
    await open('/settings/general')
    const nav = $('settings-nav')!
    const top = nav.firstElementChild!
    expect(top.querySelector('[data-testid="avatar-initial"], img')).not.toBeNull()
    expect(top.textContent).toBe('TTesterBetreiber') // the initial, the name, the role
    expect(top.getAttribute('data-part')).toBe('settings-account')
    for (const group of ['account', 'server']) expect(box.querySelector(`#settings-group-${group}`)!.className).toContain('uppercase')
    for (const entry of nav.querySelectorAll('a')) expect(entry.querySelector('svg'), entry.textContent!).not.toBeNull()
    expect($('settings-nav-about')!.parentElement!.className).toContain('border-t')
  })

  it('TIME-2 AC-7 every card of the settings has its head: a symbol or the mark of an app in an accent box, and a title', async () => {
    serve(server())
    for (const slug of [...ACCOUNT_AREAS, ...SERVER_AREAS, 'about']) {
      await open(`/settings/${slug}`)
      for (const card of box.querySelectorAll<HTMLElement>('[data-part="settings-main"] [data-card]')) {
        // A box inside a card is part of that card, not a card of its own.
        if (card.parentElement?.closest('[data-card]')) continue
        expect(card.querySelector('h2')?.textContent, `${slug} ${card.dataset.card}`).toBeTruthy()
        // The kit's tile: a symbol on the accent at 14 %, or the mark of another app (nextasks, nexpaper) or nextime's.
        expect(card.querySelector('svg, img'), `${slug} ${card.dataset.card}`).not.toBeNull()
      }
    }
  })

  it('TIME-2 AC-7 a narrow page shows the list at /settings and the area with a way back elsewhere', async () => {
    serve(server(me()))
    await open('/settings')
    expect($('settings')!.dataset.list).toBe('true')
    await open('/settings/appearance')
    // The page's own width decides: the navigation shows beside the area from 700 px, below only the area.
    expect(box.querySelector('[data-part="settings-side"]')!.className).toMatch(/(^| )hidden( |$)/)
    expect(box.querySelector('[data-part="settings-side"]')!.className).toContain('@min-[700px]:block')
    const back = $('settings-back')!
    expect(back.getAttribute('href')).toBe('/settings')
    expect(back.textContent).toBe('Einstellungen')
    await click(back)
    expect(where()).toBe('/settings')
  })

  it('TIME-2 AC-7 the list of a member has its group only, the operator\'s both', async () => {
    serve(server(me()))
    await open('/settings')
    expect([...$('settings-nav')!.querySelectorAll('[data-group]')].map((group) => group.getAttribute('data-group'))).toEqual(['account'])
    serve(server())
    await open('/settings')
    expect([...$('settings-nav')!.querySelectorAll('[data-group]')].map((group) => group.getAttribute('data-group'))).toEqual(['account', 'server'])
  })
})

describe('every card in its area (TIME-2 AC-7)', () => {
  it.each([
    ['profile', ['profile', 'sign-out']],
    ['security', ['security']],
    // "Aussehen" of the account module first, then nextime's own (plan "Einstellungen", Darstellung).
    ['appearance', ['appearance', 'week-start', 'week-numbers', 'clock', 'duplicates', 'month-style']],
    ['notifications', ['notify-ways', 'reminders', 'reminder-defaults', 'reminder-devices']],
    // The other nex apps are the cards of the shared module verbinden, one per partner of the catalog.
    ['connections', ['devices', 'install', 'nextasks', 'nexpaper', 'nexmail', 'nexfolk', 'nexdiary', 'api-keys', 'ai-tools']],
    ['general', ['limits', 'features', 'interfaces', 'nex-apps', 'languages']],
    ['sign-in', ['sign-in-ready', 'sign-in', 'sign-in-providers', 'sign-in-authentik']],
    ['mail', ['mail']],
    ['storage', ['storage', 'backups']],
    ['google', ['google']],
    // "Auf iPhone und Co" is the calendar account alone; the iPhone app is under Connections (E4).
    ['devices', ['devices-new', 'devices-list']],
    // The module's list, open invitations only when there are, "Was du siehst", the key module's overview.
    ['accounts', ['accounts', 'accounts-what-you-see', 'all-api-keys']],
    ['log', ['log']],
    ['about', ['about', 'about-updates', 'about-built-with']],
  ])('TIME-2 AC-7 %s shows its cards and no others', async (slug, ids) => {
    serve(server())
    await open(`/settings/${slug}`)
    expect(cards()).toEqual(ids)
  })

  it('TIME-3 AC-7 Appearance holds light or dark, the week, clock, zone and duplicate events; the language is in the profile', async () => {
    serve(server(me()))
    await open('/settings/appearance')
    expect($('display-fields')).not.toBeNull()
    for (const id of ['display-week-start', 'display-week-numbers', 'display-clock', 'display-duplicates', 'display-month-style']) expect($(id), id).not.toBeNull()
    expect(box.textContent).toContain('Hell oder dunkel')
    expect($('language-select')).toBeNull()
    // The time zone is the profile's now, under the language (plan "Einstellungen", E14).
    expect($('display-zone')).toBeNull()
    await open('/settings/profile')
    expect($('display-fields')).toBeNull()
    expect($('language-select')!.compareDocumentPosition($('display-zone')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // "Anmeldung mit Passwort / über …" is gone from the profile: Security says it.
    expect($('sign-in-way')).toBeNull()
    expect($('profile-picture')).not.toBeNull()
    // As the shared account module lays it out (konten 0.3.7): picture, then display name, username and address one
    // under the other with one "Speichern", the language, the role small at the end.
    const profile = box.querySelector('#profile')!
    const order = ['profile-picture', 'language-select'].map((id) => profile.querySelector(`[data-testid="${id}"]`)!)
    expect(order[0].compareDocumentPosition(order[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const form = profile.querySelector('form')!
    expect(form.className).toContain('flex-col')
    expect([...form.querySelectorAll('input')].map((field) => field.type)).toEqual(['text', 'text', 'email'])
    expect(byText('Speichern', form)).toBeDefined()
    const role = $('profile-role')!
    expect(role.textContent).toContain('Mitglied')
    expect($('language-select')!.compareDocumentPosition(role) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // Below it a card of its own: "Abmelden" with its line and button.
    expect(cards()).toEqual(['profile', 'sign-out'])
    expect(box.querySelector('#sign-out')!.textContent).toContain('Beendet deine Sitzung in diesem Browser. Deine Kalender bleiben, wie sie sind.')
    expect(byText('Abmelden', box.querySelector<HTMLElement>('#sign-out')!)).toBeDefined()
  })

  it('TIME-2 EC-12 connections is reachable and shows the keys for programs (TIME-26)', async () => {
    serve(server(me()))
    await open('/settings/connections')
    expect(heading()).toBe(i18n.t('settings.areas.connections.title'))
    expect($('later')).toBeNull()
    expect(box.querySelector('[data-card="api-keys"]')).not.toBeNull()
    expect(box.querySelector('[data-card="ai-tools"]')).not.toBeNull()
  })

  it('TIME-2 AC-7 "Kalender und Konten" is an area of its own', async () => {
    serve(server(me()))
    await open('/settings/calendars')
    expect($('calendars-area')).not.toBeNull()
    // Its content is the list of connected sources (TIME-15 AC-10), no longer "comes later".
    expect($('calendars-area')!.querySelector('[data-testid="sources"]')).not.toBeNull()
    expect($('calendars-area')!.querySelector('[data-testid="later"]')).toBeNull()
  })

  it('TIME-2 AC-7 "Google einrichten" has the switch, which waits for the setup', async () => {
    serve(server())
    await open('/settings/google')
    const area = $('google-area')!
    const toggle = area.querySelector<HTMLInputElement>('input[type="checkbox"]')!
    expect(box.querySelector(`label[for="${toggle.id}"]`)!.textContent).toBe('Google-Anbindung (nicht eingerichtet)')
    expect(toggle.disabled).toBe(true)
  })
})

describe('"Über nextime" in the settings', () => {
  it('TIME-1 AC-7 /settings/about keeps the navigation, marks its entry last and shows the version and a newer one', async () => {
    serve(server())
    await open('/settings/about')
    expect(where()).toBe('/settings/about')
    expect(current()).toEqual(['settings-nav-about'])
    expect($('settings-nav-about')!.getAttribute('aria-current')).toBe('page')
    expect(heading()).toBe('Über nextime')
    expect($('settings-area-about')!.textContent).toContain('Version, Lizenz, Herkunft und ob es eine neuere Version gibt.')
    expect($('about-version')!.textContent).toBe('0.1.0')
    expect($('about-facts')!.textContent).toContain('Version 0.2.0 ist erschienen')
    expect($('about-update-state')!.textContent).toContain('Version 0.2.0 ist erschienen')
    expect(cards()).toEqual(['about', 'about-updates', 'about-built-with'])
    expect(box.textContent).toContain('Gebaut mit')
  })

  it('TIME-1 AC-11 the operator may ask now and switches the daily check here, as in nexnotes', async () => {
    serve(server())
    await open('/settings/about')
    await click($('about-check'))
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/about/updates/check')).toBe(true)
    const toggle = box.querySelector<HTMLInputElement>('[data-card="about-updates"] input[type="checkbox"]')!
    expect(box.querySelector(`label[for="${toggle.id}"]`)!.textContent).toBe('Einmal am Tag nachsehen')
    expect(toggle.checked).toBe(true)
    await click(toggle)
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/about/updates')?.body).toEqual({ update_check: false })
  })

  it('TIME-2 AC-7 a member reaches "Über nextime" too and sees only the own version, no update card (updates U15)', async () => {
    serve(server(me()))
    await open('/settings/about')
    expect(where()).toBe('/settings/about')
    expect(current()).toEqual(['settings-nav-about'])
    expect($('about-version')!.textContent).toBe('0.1.0')
    expect($('about-facts')!.textContent).not.toContain('ist erschienen')
    expect(box.querySelector('[data-card="about-updates"]')).toBeNull()
    expect($('about-check')).toBeNull()
    // A member's page never asks for the state of the check.
    expect(calls.some((call) => call.path === '/api/about/updates')).toBe(false)
  })
})
