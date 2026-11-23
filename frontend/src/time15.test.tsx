/** TIME-15 in the interface: "Kalender hinzufügen" with its seven ways, iCloud with its guide and another CalDAV server,
 * the choice of calendars, "Kalender und Konten" with the state of every source, disconnecting, the groups in the
 * sidebar, and in the views "wartet", "Stand 15:52", the hint while a source does not answer and the refused change. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import type { CalendarList } from './features/calendars/api'
import type { Sources } from './features/sources/api'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { choices, row, sources } from './sourceFakes'
import { AuthProvider } from './state/auth'
import { calls, click, ok, render, serve, settle, signedIn, start, stop, type, type Answer } from './testing'
import { at, NOW, robin } from './viewFakes'

/** What a member types as an app-specific password (made of parts: no secret scanner takes it for a real one). */
const TYPED_APP_PASSWORD = ['abcd', 'efgh', 'ijkl', 'mnop'].join('-')
const NEW_APP_PASSWORD = ['wxyz', 'wxyz', 'wxyz', 'wxyz'].join('-')

type More = (method: string, path: string, body: unknown) => Answer | undefined

const OWN = calendarList([calendar(1, 'Familie', { default: true }), calendar(2, 'Verein', { color: 'green' })])

function server(found: Sources, more: More = () => undefined, list: CalendarList = OWN) {
  serve(
    signedIn(robin, (method, path, body) => {
      const extra = more(method, path, body)
      if (extra) return extra
      if (path === '/api/sources' && method === 'GET') return ok(found)
      if (path.startsWith('/api/calendars')) return ok(list)
      if (path.startsWith('/api/collections')) return ok({ collections: [] })
      if (path.startsWith('/api/invitations')) return ok([])
      return undefined
    }),
  )
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

async function addCalendar(): Promise<void> {
  await click(one('add-calendar'))
}

describe('TIME-15 "Kalender hinzufügen"', () => {
  it('TIME-15 AC-7 shows the seven ways with a sentence and a mark each, and no count up to 20 calendars from all sources', async () => {
    const list = calendarList([calendar(1, 'Familie', { default: true }), ...Array.from({ length: 8 }, (_, index) => calendar(20 + index, `Q${index}`, { group: 'icloud', origin: { id: 1, kind: 'icloud', label: 'm', state: 'ok', synced_at: null } }))])
    server(sources([]), undefined, list)
    await open()
    await addCalendar()
    const ways = all('add-calendar-dialog')[0].querySelectorAll('[data-testid^="way-"]')
    expect([...ways].map((way) => way.querySelector('strong')!.textContent)).toEqual(['Eigener Kalender', 'iCloud', 'Google', 'Anderer CalDAV-Server', 'Abo-Link', 'Feiertage und Ferien', 'Datei laden'])
    expect(all('tag-of-way').map((tag) => tag.textContent)).toEqual(['lesen und schreiben', 'lesen und schreiben', 'nur lesen', 'lesen und schreiben', 'nur lesen', 'nur lesen', 'einmalig'])
    expect(one('calendar-count')).toBeNull()
    // "Datei laden" works since TIME-24 (AC-5).
    expect((one('way-file') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('TIME-15 iCloud and CalDAV', () => {
  it('TIME-15 AC-8 the iCloud dialog has the guide in three steps, the link to Apple and the sentence on the password', async () => {
    server(sources([]))
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    const dialog = one('connect-dialog')!
    expect(dialog.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('iCloud verbinden')
    expect(all('apple-steps')[0].querySelectorAll('li').length).toBe(3)
    expect([...all('apple-steps')[0].querySelectorAll('li')].map((step) => step.textContent)).toEqual([
      'account.apple.com öffnen und anmelden',
      '„Anmelden und Sicherheit“ › „App-spezifische Passwörter“',
      'Name „nextime“ eingeben, Passwort kopieren und hier einfügen',
    ])
    expect(one('apple-guide')!.textContent).toContain('So bekommst du es (2 Minuten)')
    expect(one('apple-page')!.getAttribute('href')).toBe('https://account.apple.com')
    expect(one('apple-page')!.textContent).toBe('Seite bei Apple öffnen')
    expect(dialog.textContent).toContain('Das Passwort liegt verschlüsselt auf deinem Server und lässt sich bei Apple jederzeit widerrufen.')
  })

  it('TIME-15 EC-24 in English the guide names Apple\'s pages and menus in English', async () => {
    await i18n.changeLanguage('en')
    server(sources([]))
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    expect(one('apple-steps')!.textContent).toContain('“Sign-In and Security” › “App-Specific Passwords”')
    expect(one('apple-page')!.textContent).toBe("Open Apple's page")
  })

  it('TIME-15 AC-1 "Abbrechen" connects nothing and saves nothing', async () => {
    server(sources([]))
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    await click([...one('connect-dialog')!.querySelectorAll('button')].find((button) => button.textContent === 'Abbrechen'))
    expect(one('connect-dialog')).toBeNull()
    expect(posted('/api/sources')).toEqual([])
  })

  it('TIME-15 AC-1 AC-9 AC-6 AC-14 EC-13 connecting leads to "iCloud verbunden" with colors, ticks, "nur lesen" and "neu"; "Fertig" sends the ticked ones', async () => {
    server(sources([]), (method, path) => {
      if (path === '/api/sources/dav' && method === 'POST') return { status: 201, body: choices() }
      if (path === '/api/sources/1/calendars' && method === 'PUT') return ok(sources([row(1)]))
      return undefined
    })
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    type(one('connect-username') as HTMLInputElement, '  familie@example.com ')
    type(one('connect-password') as HTMLInputElement, ' abcd-efgh-ijkl-mnop ')
    await click(one('connect-go'))
    expect(posted('/api/sources/dav')[0].body).toMatchObject({ kind: 'icloud', server: '', username: 'familie@example.com', password: TYPED_APP_PASSWORD })
    const dialog = one('choose-dialog')!
    expect(dialog.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('iCloud verbunden')
    expect(dialog.textContent).toContain('Welche Kalender sollen in nextime erscheinen?')
    expect(all('choose-name').map((name) => name.textContent)).toEqual(['Arbeit', 'Persönlich', 'Team (von Chris)', 'Garten'])
    expect(all('choose-box').map((box) => (box as HTMLInputElement).checked)).toEqual([true, true, false, false])
    expect(dialog.querySelectorAll('[data-color]').length).toBe(4)
    expect(all('mark-read-only').map((mark) => mark.closest('li')!.textContent)).toEqual(['Team (von Chris)nur lesen'])
    expect(all('mark-new').map((mark) => mark.closest('li')!.textContent)).toEqual(['Gartenneu'])
    await act(async () => (all('choose-box')[1] as HTMLInputElement).click())
    await act(async () => (all('choose-box')[3] as HTMLInputElement).click())
    await click(one('choose-done'))
    expect(posted('/api/sources/1/calendars')[0].body).toEqual({ urls: ['/arbeit/', '/garten/'] })
    expect(one('choose-dialog')).toBeNull()
  })

  it('TIME-15 EC-4 the normal Apple password is refused at the field, with the sentence of the spec', async () => {
    server(sources([]), (method, path) =>
      path === '/api/sources/dav' && method === 'POST' ? { status: 422, body: { detail: { code: 'apple_password_not_app_specific', message: 'No.', field: 'password' } } } : undefined,
    )
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    type(one('connect-username') as HTMLInputElement, 'familie@example.com')
    type(one('connect-password') as HTMLInputElement, 'MeinPasswort1')
    await click(one('connect-go'))
    expect(one('connect-password-problem')!.textContent).toBe('Apple lehnt ab. Das sieht nach deinem normalen Apple-Passwort aus, nextime braucht ein app-spezifisches (Form xxxx-xxxx-xxxx-xxxx).')
    expect(one('connect-password')!.getAttribute('aria-invalid')).toBe('true')
    expect(one('choose-dialog')).toBeNull()
  })

  it('TIME-15 AC-2 EC-13 another CalDAV server asks for address, name and password; an address without a calendar server is refused there', async () => {
    server(sources([]), (method, path) =>
      path === '/api/sources/dav' && method === 'POST' ? { status: 422, body: { detail: { code: 'no_calendar_server', message: 'No.', field: 'server' } } } : undefined,
    )
    await open()
    await addCalendar()
    await click(one('way-caldav'))
    const labels = [...one('connect-dialog')!.querySelectorAll('label')].map((label) => label.textContent)
    expect(labels).toEqual(['Adresse', 'Name', 'Passwort'])
    type(one('connect-server') as HTMLInputElement, ' https://cloud.example.net ')
    type(one('connect-username') as HTMLInputElement, 'verein')
    type(one('connect-password') as HTMLInputElement, 'geheim')
    await click(one('connect-go'))
    expect(posted('/api/sources/dav')[0].body).toMatchObject({ kind: 'caldav', server: 'https://cloud.example.net', username: 'verein', password: 'geheim' })
    expect(one('connect-server-problem')!.textContent).toBe('Unter dieser Adresse antwortet kein Kalenderserver.')
    expect(one('choose-dialog')).toBeNull()
  })

  it('TIME-15 EC-14 the same account again leads to choosing the calendars of the existing one', async () => {
    server(sources([row(4)]), (method, path) => {
      if (path === '/api/sources/dav' && method === 'POST') return { status: 409, body: { detail: { code: 'source_exists', message: 'No.', source_id: 4 } } }
      if (path === '/api/sources/4/calendars' && method === 'GET') return ok(choices({ source_id: 4 }))
      return undefined
    })
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    type(one('connect-username') as HTMLInputElement, 'familie@example.com')
    type(one('connect-password') as HTMLInputElement, 'abcd-efgh-ijkl-mnop')
    await click(one('connect-go'))
    expect(one('choose-note')!.textContent).toBe('Dieses Konto ist schon verbunden. Hier wählst du seine Kalender.')
    expect(one('choose-dialog')!.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('iCloud-Kalender wählen')
  })

  it('TIME-15 EC-15 EC-17 two clicks on "Verbinden" and on "Fertig" send once; while connecting the dialog says it checks', async () => {
    server(sources([]), (method, path) => {
      if (path === '/api/sources/dav' && method === 'POST') return { status: 201, body: choices() }
      if (path === '/api/sources/1/calendars' && method === 'PUT') return ok(sources([row(1)]))
      return undefined
    })
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    type(one('connect-username') as HTMLInputElement, 'familie@example.com')
    type(one('connect-password') as HTMLInputElement, 'abcd-efgh-ijkl-mnop')
    const go = one('connect-go')!
    act(() => {
      go.click()
      go.click()
    })
    expect(go.textContent).toBe('Prüft …')
    await settle()
    expect(posted('/api/sources/dav')).toHaveLength(1)
    const done = one('choose-done')!
    act(() => {
      done.click()
      done.click()
    })
    await settle()
    expect(posted('/api/sources/1/calendars')).toHaveLength(1)
  })

  it('TIME-15 EC-10 an account without calendars says so in the choice', async () => {
    server(sources([]), (method, path) => (path === '/api/sources/dav' && method === 'POST' ? { status: 201, body: choices({ calendars: [] }) } : undefined))
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    type(one('connect-username') as HTMLInputElement, 'familie@example.com')
    type(one('connect-password') as HTMLInputElement, 'abcd-efgh-ijkl-mnop')
    await click(one('connect-go'))
    expect(one('choose-none')!.textContent).toBe('Dieses Konto hat keine Kalender.')
  })

  it('TIME-15 EC-11 ticking past 20 calendars goes, with the hint of TIME-4 AC-6', async () => {
    const list = calendarList(Array.from({ length: 19 }, (_, index) => calendar(index + 1, `K${index}`)))
    server(sources([]), (method, path) => (path === '/api/sources/dav' && method === 'POST' ? { status: 201, body: choices() } : undefined), list)
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    type(one('connect-username') as HTMLInputElement, 'familie@example.com')
    type(one('connect-password') as HTMLInputElement, 'abcd-efgh-ijkl-mnop')
    await click(one('connect-go'))
    expect(one('choose-over')!.textContent).toBe('Du hast 21 Kalender; ab 20 kann nextime langsamer werden.')
  })

  it('TIME-15 EC-16 names from the source stay text', async () => {
    server(sources([]), (method, path) =>
      path === '/api/sources/dav' && method === 'POST'
        ? { status: 201, body: choices({ calendars: [{ url: '/x/', name: '<img src=x onerror=alert(1)><b>Böse</b>', color: 'red', read_only: false, new: false, chosen: true }] }) }
        : undefined,
    )
    await open()
    await addCalendar()
    await click(one('way-icloud'))
    type(one('connect-username') as HTMLInputElement, 'familie@example.com')
    type(one('connect-password') as HTMLInputElement, 'abcd-efgh-ijkl-mnop')
    await click(one('connect-go'))
    expect(one('choose-name')!.textContent).toBe('<img src=x onerror=alert(1)><b>Böse</b>')
    expect(one('choose-list')!.querySelector('img, b')).toBeNull()
  })
})

describe('TIME-15 "Kalender und Konten"', () => {
  it('TIME-15 AC-10 AC-5 AC-13 one row per source with kind, account and calendars and state; the sentence below', async () => {
    server(sources([row(1), row(2, { kind: 'caldav', label: 'verein · cloud.example.net', calendars: [{ id: 21, name: 'Vorstand', color: 'green', read_only: false }] }), row(3, { label: 'zweite@example.com', calendars: [] })]))
    await open('/settings/calendars')
    expect(all('source-kind').map((kind) => kind.textContent)).toEqual(['iCloud', 'CalDAV', 'iCloud'])
    expect(all('source-line').map((line) => line.textContent)).toEqual(['familie@example.com · Arbeit, Persönlich', 'verein · cloud.example.net · Vorstand', 'zweite@example.com'])
    expect(all('source-state').map((state) => state.textContent)).toEqual(['abgeglichen vor 1 Minute', 'abgeglichen vor 1 Minute', 'abgeglichen vor 1 Minute'])
    expect(all('source-state').every((state) => state.dataset.warn === 'false')).toBe(true)
    expect(all('source-choose')).toHaveLength(3)
    expect(all('source-remove').map((button) => button.textContent)).toEqual(['Trennen', 'Trennen', 'Trennen'])
    expect(one('sources-add')!.textContent).toBe('Kalender hinzufügen')
    expect(one('sources-below')!.textContent).toBe('Trennen entfernt die Kalender und die gespeicherten Zugangsdaten aus nextime. In iCloud und Google bleibt alles, wie es ist.')
  })

  it('TIME-15 EC-1 AC-11 an expired access stands in orange with "Neu verbinden", a hint names the calendars; a new password sends', async () => {
    const expired = row(1, { state: 'expired', since: '2026-11-25T17:02:00Z', synced_at: '2026-11-25T17:02:00Z', problem: 'credentials' })
    server(sources([expired]), (method, path) => (path === '/api/sources/1/reconnect' && method === 'POST' ? ok(sources([row(1)])) : undefined))
    await open('/settings/calendars')
    const state = one('source-state')!
    expect(state.textContent).toBe('Zugang abgelaufen seit gestern 18:02')
    expect(state.dataset.warn).toBe('true')
    expect(state.className).toContain('text-warn-500')
    expect(one('expired-hint')!.textContent).toBe('iCloud-Zugang abgelaufen. „Arbeit“ und „Persönlich“ zeigen weiter den Stand von gestern 18:02; Ändern geht erst nach dem neuen Verbinden.')
    await click(one('source-reconnect'))
    type(one('reconnect-password') as HTMLInputElement, ' wxyz-wxyz-wxyz-wxyz ')
    await click(one('reconnect-go'))
    expect(posted('/api/sources/1/reconnect')[0].body).toEqual({ password: NEW_APP_PASSWORD })
    expect(one('reconnect-dialog')).toBeNull()
  })

  it('TIME-15 EC-8 disconnecting with waiting changes says first how many get lost, then sends', async () => {
    server(sources([row(1, { waiting: 2 })]), (method, path) => (method === 'DELETE' && path.startsWith('/api/sources/1') ? ok(sources([])) : undefined))
    await open('/settings/calendars')
    await click(one('source-remove'))
    expect(one('remove-dialog')!.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('„iCloud“ trennen?')
    expect(one('remove-waiting')!.textContent).toBe('2 Änderungen sind noch nicht in iCloud angekommen und gehen mit dem Trennen verloren.')
    expect(posted('/api/sources/1')).toEqual([])
    await click(one('remove-go'))
    expect(posted('/api/sources/1')[0].path).toBe('/api/sources/1?drop_waiting=true')
  })

  it('TIME-15 AC-5 disconnecting without waiting changes sends at once and the row goes', async () => {
    let now = sources([row(1)])
    server(now, (method, path) => {
      if (method === 'DELETE' && path.startsWith('/api/sources/1')) now = sources([])
      return path.startsWith('/api/sources') ? ok(now) : undefined
    })
    await open('/settings/calendars')
    await click(one('source-remove'))
    expect(one('remove-waiting')).toBeNull()
    await click(one('remove-go'))
    expect(posted('/api/sources/1')[0].path).toBe('/api/sources/1')
    expect(all('source-row')).toHaveLength(0)
  })

  it('TIME-15 EC-9 without a source a short hint and one button at the top; the sidebar has no empty group', async () => {
    server(sources([]))
    await open('/settings/calendars')
    expect(one('sources-empty')!.textContent).toContain('Noch keine Konten oder Abos verbunden.')
    // "Kalender hinzufügen" once, at the top.
    expect(one('sources-empty')!.querySelector('button')).toBeNull()
    expect(all('sources-add')).toHaveLength(1)
    expect(one('calendar-group-icloud')).toBeNull()
    expect(document.querySelector('[role="alert"]')).toBeNull()
    await click(one('sources-add'))
    expect(one('add-calendar-dialog')).not.toBeNull()
  })

  it('TIME-15 AC-10 own calendars listed first, by group as in the sidebar, with their marks, eye and menu (TIME-4 settings list)', async () => {
    server(sources([]))
    await open('/settings/calendars')
    const list = one('settings-calendars')!
    const rows = [...list.querySelectorAll<HTMLElement>('[data-testid="settings-calendar"]')]
    expect(rows.length).toBeGreaterThan(0)
    expect(rows[0].textContent).toContain('Standard')
    expect(list.querySelectorAll('[data-testid="calendar-eye"]')).toHaveLength(rows.length)
    // Before the sources.
    const area = one('calendars-area')!
    const order = [...area.querySelectorAll('[data-testid="settings-calendars"], [data-testid="sources-head"]')].map((node) => node.getAttribute('data-testid'))
    expect(order).toEqual(['settings-calendars', 'sources-head'])
    // A click on an own calendar opens its dialog.
    await click(rows[0])
    expect(one('calendar-dialog')).not.toBeNull()
  })

  it('TIME-15 AC-14 EC-18 EC-12 a new calendar, unreadable events, still loading and over 20,000 events are said quietly', async () => {
    server(sources([row(1, { new: 1, unreadable: 3 }), row(2, { loading: true, label: 'zweite@example.com' })], { events_total: 21500 }))
    await open('/settings/calendars')
    expect(one('source-new')!.textContent).toBe('Ein neuer Kalender wartet unter „Kalender wählen“.')
    expect(one('source-unreadable')!.textContent).toBe('3 Termine waren nicht lesbar.')
    expect(all('source-state')[1].textContent).toBe('lädt noch …')
    expect(one('events-over')!.textContent).toBe('Du hast 21.500 Termine; ab 20.000 kann nextime langsamer werden.')
  })

  it('TIME-15 EC-2 EC-1 "Erneut holen" fetches an unreachable source again, once however often clicked', async () => {
    let now = sources([row(1, { state: 'unreachable', since: '2026-11-26T14:52:00Z', synced_at: '2026-11-26T14:52:00Z' })])
    server(now, (method, path) => {
      if (path === '/api/sources/1/refresh' && method === 'POST') now = sources([row(1)])
      return path.startsWith('/api/sources') ? ok(now) : undefined
    })
    await open('/settings/calendars')
    expect(one('source-state')!.textContent).toBe('gerade nicht erreichbar, Stand von 15:52')
    const again = one('source-refresh')!
    act(() => {
      again.click()
      again.click()
    })
    await settle()
    expect(posted('/api/sources/1/refresh')).toHaveLength(1)
    expect(one('source-state')!.textContent).toBe('abgeglichen vor 1 Minute')
  })

  it('TIME-15 AC-9 "Kalender wählen" opens the choice of the account', async () => {
    server(sources([row(1)]), (method, path) => (path === '/api/sources/1/calendars' && method === 'GET' ? ok(choices()) : undefined))
    await open('/settings/calendars')
    await click(one('source-choose'))
    expect(one('choose-dialog')!.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('iCloud-Kalender wählen')
  })

  it('TIME-15 EC-8 leaving out a calendar with waiting changes says first how many get lost, then sends', async () => {
    server(sources([row(1)]), (method, path, body) => {
      if (path === '/api/sources/1/calendars' && method === 'GET') return ok(choices())
      if (path === '/api/sources/1/calendars' && method === 'PUT') {
        if ((body as { drop_waiting?: boolean }).drop_waiting) return ok(sources([row(1)]))
        return { status: 409, body: { detail: { code: 'changes_waiting', message: 'No.', count: 3 } } }
      }
      return undefined
    })
    await open('/settings/calendars')
    await click(one('source-choose'))
    await click(all('choose-box')[0])
    await click(one('choose-done'))
    expect(one('choose-waiting')!.textContent).toBe(
      '3 Änderungen in den abgewählten Kalendern sind noch nicht in iCloud angekommen und gehen verloren. „Fertig“ wählt sie trotzdem ab.',
    )
    expect(one('choose-dialog')).not.toBeNull()
    await click(one('choose-done'))
    const sent = posted('/api/sources/1/calendars').map((call) => call.body)
    expect(sent).toEqual([{ urls: ['/persoenlich/'] }, { urls: ['/persoenlich/'], drop_waiting: true }])
    expect(one('choose-dialog')).toBeNull()
  })
})

describe('TIME-15 in the sidebar and the views', () => {
  const origin = (id: number, kind: 'icloud' | 'caldav', label: string, state: 'ok' | 'unreachable' = 'ok') => ({ id, kind, label, state, synced_at: '2026-11-26T14:52:00Z' })

  it('TIME-15 AC-9 AC-13 the sidebar has "iCloud" and a group per CalDAV account under its name', async () => {
    const list = calendarList([
      calendar(1, 'Familie', { default: true }),
      calendar(11, 'Arbeit', { group: 'icloud', origin: origin(1, 'icloud', 'familie@example.com') }),
      calendar(21, 'Vorstand', { group: 'caldav', origin: origin(2, 'caldav', 'verein · cloud.example.net') }),
      calendar(31, 'Projekte', { group: 'caldav', origin: origin(3, 'caldav', 'büro · dav.example.org') }),
    ])
    server(sources([]), undefined, list)
    await open()
    const heads = [...one('calendar-list')!.querySelectorAll('h3')].map((head) => head.textContent)
    expect(heads).toEqual(['Eigene', 'iCloud', 'verein · cloud.example.net', 'büro · dav.example.org'])
  })

  it('TIME-15 EC-2 EC-5 "wartet" and "Stand 15:52" at events; the hint with "Erneut versuchen" goes once the source answers', async () => {
    const list = calendarList([calendar(1, 'Familie', { default: true }), calendar(11, 'Arbeit', { group: 'icloud', color: 'teal', origin: origin(1, 'icloud', 'familie@example.com', 'unreachable') })])
    let answering = false
    server(
      sources([row(1, { state: 'unreachable', synced_at: '2026-11-26T14:52:00Z' })]),
      (method, path) => {
        if (path === '/api/sources/1/refresh' && method === 'POST') {
          answering = true
          return ok(sources([row(1)]))
        }
        if (path === '/api/sources' && answering) return ok(sources([row(1)]))
        if (path.startsWith('/api/events?'))
          return ok([at(1, 'Elternabend', '2026-11-26 19:00', '2026-11-26 20:00', { waiting: true }), at(11, 'Kundentermin', '2026-11-26 17:00', '2026-11-26 18:00')])
        return undefined
      },
      list,
    )
    await open()
    expect(one('unreachable-hint')!.textContent).toBe('iCloud gerade nicht erreichbar. Arbeit und Persönlich zeigen den Stand von 15:52.Erneut versuchen')
    expect(all('waiting').map((mark) => mark.textContent)).toEqual(['wartet'])
    expect(all('state-of').map((mark) => mark.textContent)).toEqual(['Stand 15:52'])
    await click(one('unreachable-retry'))
    expect(posted('/api/sources/1/refresh')).toHaveLength(1)
    expect(one('unreachable-hint')).toBeNull()
  })

  it('TIME-15 EC-6 a refused change: the hint names it, "Als neuen Termin anlegen" and "Verwerfen"', async () => {
    const notice = { id: 9, title: 'Werkstatt', source: 'icloud' as const, calendar: 'Arbeit', calendar_id: 11, lost: { title: 'Werkstatt', all_day: false, start: '2026-11-27T09:00', end: '2026-11-27T09:45', time_zone: 'Europe/Berlin' }, created_at: '2026-11-26T15:00:00Z' }
    let now = sources([row(1)], { notices: [notice] })
    server(now, (method, path) => {
      if (path.startsWith('/api/sources/notices/9') && method === 'POST') now = sources([row(1)], { created_event: 77 })
      return path.startsWith('/api/sources') ? ok(now) : undefined
    })
    await open('/week')
    expect(one('notice-text')!.textContent).toBe('iCloud hat deine Änderung an „Werkstatt“ nicht übernommen (Fr., 27. November · 09:00–09:45). Der Termin zeigt wieder den Stand aus iCloud.')
    expect(one('notice-create')!.textContent).toBe('Als neuen Termin anlegen')
    expect(one('notice-discard')!.textContent).toBe('Verwerfen')
    await click(one('notice-create'))
    expect(posted('/api/sources/notices/9')[0].body).toEqual({ create: true })
    expect(one('notice')).toBeNull()
    expect(one('toast')!.textContent).toBe('„Werkstatt“ steht jetzt als neuer Termin im Kalender.')
  })

  it('TIME-15 EC-6 "Verwerfen" drops the notice', async () => {
    const notice = { id: 9, title: 'Werkstatt', source: 'caldav' as const, calendar: 'Vorstand', calendar_id: 21, lost: null, created_at: '2026-11-26T15:00:00Z' }
    server(sources([row(1)], { notices: [notice] }), (method, path) => (path.startsWith('/api/sources/notices/9') && method === 'POST' ? ok(sources([row(1)])) : undefined))
    await open('/month')
    expect(one('notice-text')!.textContent).toBe('CalDAV hat das Löschen von „Werkstatt“ nicht übernommen. Der Termin zeigt wieder den Stand aus CalDAV.')
    expect(one('notice-create')).toBeNull()
    await click(one('notice-discard'))
    expect(posted('/api/sources/notices/9')[0].body).toEqual({ create: false })
  })
})

describe('TIME-15 asked for while testing (2026-11-23)', () => {
  it('TIME-15 remove from nextime: "…" of an iCloud calendar asks, names the way back, counts waiting changes, then a toast', async () => {
    const list = calendarList([calendar(1, 'Familie', { default: true }), calendar(20, 'Arbeit', { group: 'icloud', origin: { id: 7, kind: 'icloud', label: 'm', state: 'ok', synced_at: null } })])
    let tries = 0
    server(sources([]), (method, path) => {
      if (method !== 'DELETE' || !path.startsWith('/api/sources/7/calendars/20')) return undefined
      tries += 1
      return tries === 1 ? { status: 409, body: { detail: { code: 'changes_waiting', message: 'x', count: 2 } } } : ok(sources([]))
    }, list)
    await open()
    await click(document.querySelector('[aria-label="Mehr zu „Arbeit“"], [aria-label*="Arbeit"][aria-haspopup="menu"]'))
    expect(one('calendar-delete')).toBeNull()
    await click(one('calendar-leave'))
    expect(document.querySelector('[role="dialog"]')!.textContent).toContain('„Arbeit“ aus nextime entfernen?')
    expect(one('leave-calendar-dialog')!.textContent).toContain('In iCloud bleibt er, wie er ist. Wieder aufnehmen kannst du ihn unter Kalender und Konten › Kalender wählen.')
    await click(one('leave-calendar-confirm'))
    expect(one('leave-calendar-waiting')!.textContent).toContain('2 Änderungen')
    await click(one('leave-calendar-confirm'))
    expect(calls.filter((call) => call.method === 'DELETE').map((call) => call.path)).toEqual(['/api/sources/7/calendars/20', '/api/sources/7/calendars/20?drop_waiting=true'])
    expect(one('leave-calendar-dialog')).toBeNull()
    expect(one('toast')!.textContent).toBe('„Arbeit“ ist aus nextime entfernt.')
    expect(one('notice-undo')).toBeNull()
  })
})
