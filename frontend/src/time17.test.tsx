/** TIME-17 in the interface: "Kalender abonnieren" with its preview, a sign-in and the warnings; "Feiertage und Ferien"
 * with country, regions and school holidays; their rows on "Kalender und Konten". */
import { act } from 'react'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import type { Sources } from './features/sources/api'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { COUNTRIES, preview, row, sources } from './sourceFakes'
import { AuthProvider } from './state/auth'
import { byText, calls, click, ok, render, serve, settle, signedIn, start, stop, type, type Answer } from './testing'
import { NOW, robin } from './viewFakes'

type More = (method: string, path: string, body: unknown) => Answer | undefined

function server(found: Sources, more: More = () => undefined) {
  serve(
    signedIn(robin, (method, path, body) => {
      const extra = more(method, path, body)
      if (extra) return extra
      if (path === '/api/sources' && method === 'GET') return ok(found)
      if (path === '/api/sources/holidays' && method === 'GET') return ok(COUNTRIES)
      if (path.startsWith('/api/calendars')) return ok(calendarList([calendar(1, 'Familie', { default: true })]))
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

async function leave(field: HTMLElement): Promise<void> {
  await act(async () => void field.dispatchEvent(new FocusEvent('focusout', { bubbles: true })))
  await settle()
}

async function subscribeDialog(): Promise<void> {
  await click(one('add-calendar'))
  await click(one('way-subscription'))
}

describe('TIME-17 subscriptions', () => {
  it('TIME-17 AC-1 AC-2 EC-5 the preview names source, count and span, read only; the name is filled in; "Abonnieren" subscribes', async () => {
    server(sources([]), (method, path) => {
      if (path === '/api/sources/subscriptions/preview' && method === 'POST') return ok(preview())
      if (path === '/api/sources/subscriptions' && method === 'POST') return { status: 201, body: { ...sources([]), created: 8 } }
      return undefined
    })
    await open()
    await subscribeDialog()
    expect(one('subscribe-dialog')!.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('Kalender abonnieren')
    expect(one('hourly')!.textContent).toBe('nextime holt Änderungen einmal in der Stunde.')
    const url = one('subscribe-url') as HTMLInputElement
    type(url, '  webcal://abfall.example.com/musterstadt.ics ')
    await leave(url)
    expect(posted('/api/sources/subscriptions/preview')[0].body).toMatchObject({ url: 'webcal://abfall.example.com/musterstadt.ics' })
    expect(one('subscribe-preview')!.querySelector('strong')!.textContent).toBe('Abfallkalender Musterstadt')
    expect(one('subscribe-summary')!.textContent).toBe('74 Termine · Januar bis Dezember 2027 · nur lesen')
    expect((one('subscribe-name') as HTMLInputElement).value).toBe('Abfallkalender Musterstadt')
    type(one('subscribe-name') as HTMLInputElement, 'Müllabfuhr')
    const background = one('subscribe-background')!
    expect(background.getAttribute('aria-checked')).toBe('true')
    expect(background.getAttribute('aria-label')).toBe('Als Hintergrund zeigen')
    await click(one('subscribe-go'))
    expect(posted('/api/sources/subscriptions/')).toHaveLength(1)
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/sources/subscriptions')!.body).toMatchObject({
      url: 'webcal://abfall.example.com/musterstadt.ics',
      name: 'Müllabfuhr',
      background: true,
      google: false,
    })
    expect(one('subscribe-dialog')).toBeNull()
  })

  it('TIME-17 AC-1 "Abbrechen" creates nothing', async () => {
    server(sources([]))
    await open()
    await subscribeDialog()
    await click([...one('subscribe-dialog')!.querySelectorAll('button')].find((button) => button.textContent === 'Abbrechen'))
    expect(one('subscribe-dialog')).toBeNull()
    expect(posted('/api/sources')).toEqual([])
  })

  it('TIME-17 EC-2 a huge subscription warns in the preview', async () => {
    server(sources([]), (method, path) => (path === '/api/sources/subscriptions/preview' && method === 'POST' ? ok(preview({ count: 15000, over: true, events_total: 6000, first: '2015-01-01', last: '2030-12-31' })) : undefined))
    await open()
    await subscribeDialog()
    type(one('subscribe-url') as HTMLInputElement, 'https://riesig.example.com/all.ics')
    await leave(one('subscribe-url')!)
    expect(one('subscribe-summary')!.textContent).toBe('15000 Termine · Januar 2015 bis Dezember 2030 · nur lesen')
    expect(one('subscribe-over')!.textContent).toBe('Mit diesem Abo hast du 21.000 Termine; ab 20.000 kann nextime langsamer werden.')
  })

  it('TIME-17 EC-3 a link that asks for a sign-in shows name and password; wrong ones are refused at the field', async () => {
    server(sources([]), (method, path, body) => {
      if (path !== '/api/sources/subscriptions/preview' || method !== 'POST') return undefined
      const login = body as { username?: string; password?: string }
      if (!login.password) return { status: 422, body: { detail: { code: 'subscription_login_needed', message: 'No.' } } }
      return { status: 422, body: { detail: { code: 'subscription_login_refused', message: 'No.', field: 'password' } } }
    })
    await open()
    await subscribeDialog()
    type(one('subscribe-url') as HTMLInputElement, 'https://verein.example.com/intern.ics')
    await leave(one('subscribe-url')!)
    expect(one('subscribe-login')).not.toBeNull()
    expect(one('dialog-problem')!.textContent).toBe('Dieser Link verlangt eine Anmeldung. Bitte Name und Passwort eintragen.')
    type(one('subscribe-username') as HTMLInputElement, 'mitglied')
    type(one('subscribe-password') as HTMLInputElement, 'falsch')
    await leave(one('subscribe-password')!)
    expect(posted('/api/sources/subscriptions/preview')[1].body).toMatchObject({ username: 'mitglied', password: 'falsch' })
    expect(one('subscribe-password-problem')!.textContent).toBe('Name oder Passwort stimmt nicht.')
  })

  it('TIME-17 EC-4 a link to something else than a calendar is refused clearly and nothing is created', async () => {
    server(sources([]), (method, path) =>
      path.startsWith('/api/sources/subscriptions') && method === 'POST' ? { status: 422, body: { detail: { code: 'subscription_not_a_calendar', message: 'No.' } } } : undefined,
    )
    await open()
    await subscribeDialog()
    type(one('subscribe-url') as HTMLInputElement, 'https://example.com/seite.html')
    await click(one('subscribe-go'))
    expect(one('subscribe-url-problem')!.textContent).toBe('Unter diesem Link liegt kein Kalender.')
    expect(one('subscribe-dialog')).not.toBeNull()
  })

  it('TIME-17 EC-6 the same link again: nextime says so and creates no second subscription', async () => {
    server(sources([]), (method, path) => (path === '/api/sources/subscriptions/preview' && method === 'POST' ? ok(preview({ exists: 4 })) : undefined))
    await open()
    await subscribeDialog()
    type(one('subscribe-url') as HTMLInputElement, 'https://abfall.example.com/musterstadt.ics')
    await leave(one('subscribe-url')!)
    expect(one('subscribe-exists')!.textContent).toBe('Diesen Link hast du schon abonniert.')
    expect((one('subscribe-go') as HTMLButtonElement).disabled).toBe(true)
  })

  it('TIME-17 EC-9 two clicks on "Abonnieren" make one subscription', async () => {
    server(sources([]), (method, path) => (path === '/api/sources/subscriptions' && method === 'POST' ? { status: 201, body: { ...sources([]), created: 8 } } : undefined))
    await open()
    await subscribeDialog()
    type(one('subscribe-url') as HTMLInputElement, 'https://abfall.example.com/musterstadt.ics')
    const go = one('subscribe-go')!
    act(() => {
      go.click()
      go.click()
    })
    await settle()
    expect(calls.filter((call) => call.method === 'POST' && call.path === '/api/sources/subscriptions')).toHaveLength(1)
  })

  it('TIME-17 EC-1 AC-5 a subscription that has not come for days: orange, "Erneut holen", "Entfernen"', async () => {
    let now = sources([row(6, { kind: 'subscription', label: 'abfall.example.com', state: 'unreachable', since: '2026-11-23T08:00:00Z', synced_at: '2026-11-23T08:00:00Z', calendars: [{ id: 61, name: 'Müllabfuhr', color: 'brown', read_only: true }] })])
    server(now, (method, path) => {
      if (method === 'DELETE' && path.startsWith('/api/sources/6')) now = sources([])
      return path.startsWith('/api/sources') && !path.startsWith('/api/sources/holidays') ? ok(now) : undefined
    })
    await open('/settings/calendars')
    expect(one('source-kind')!.textContent).toBe('Abo')
    expect(one('source-line')!.textContent).toBe('Müllabfuhr · abfall.example.com')
    expect(one('source-state')!.textContent).toBe('seit Mo., 23.11. 09:00 nicht aktualisiert, die letzten Termine bleiben')
    expect(one('source-state')!.dataset.warn).toBe('true')
    expect(one('source-refresh')!.textContent).toBe('Erneut holen')
    expect(one('source-choose')).toBeNull()
    await click(one('source-remove'))
    expect(one('remove-dialog')!.textContent).toContain('Entfernen nimmt den Kalender samt Terminen aus nextime. An der Quelle bleibt alles, wie es ist.')
    await click(one('remove-go'))
    expect(calls.filter((call) => call.method === 'DELETE').map((call) => call.path)).toEqual(['/api/sources/6'])
  })
})

describe('TIME-17 holidays and school holidays', () => {
  it('TIME-17 AC-3 AC-4 EC-17 a country, several states, all 16 to tick without scrolling sideways, the school switch and the sentence', async () => {
    server(sources([]), (method, path) => (path === '/api/sources/holidays' && method === 'PUT' ? ok(sources([])) : undefined))
    await open()
    await click(one('add-calendar'))
    await click(one('way-holidays'))
    const dialog = one('holidays-dialog')!
    expect(dialog.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('Feiertage und Ferien')
    expect([...(one('holidays-country') as HTMLSelectElement).options].map((option) => option.textContent)).toEqual(['Deutschland', 'Österreich', 'Schweiz'])
    expect(all('holidays-region')).toHaveLength(16)
    expect(one('holidays-regions')!.className).toContain('flex-wrap')
    expect(one('holidays-school')!.getAttribute('aria-label')).toBe('Schulferien zeigen')
    expect(dialog.textContent).toContain('Erscheint leise oben im Tag. Mehrere Bundesländer gehen, etwa wenn Kinder woanders zur Schule gehen.')
    const pick = (name: string) => all('holidays-region').find((region) => region.textContent === name)!
    await click(pick('Nordrhein-Westfalen'))
    await click(pick('Bayern'))
    expect(pick('Bayern').getAttribute('aria-pressed')).toBe('true')
    await click(one('holidays-go'))
    expect(posted('/api/sources/holidays')[0].body).toEqual({ country: 'DE', regions: ['NW', 'BY'], school: true })
  })

  it('TIME-17 EC-13 where school holidays are not known the switch is missing, with a sentence why; holidays still go', async () => {
    server(sources([]), (method, path) => (path === '/api/sources/holidays' && method === 'PUT' ? ok(sources([])) : undefined))
    await open()
    await click(one('add-calendar'))
    await click(one('way-holidays'))
    const country = one('holidays-country') as HTMLSelectElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(country, 'CH')
      country.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(one('holidays-school')).toBeNull()
    expect(one('holidays-no-school')!.textContent).toBe('In der Schweiz legen die Gemeinden die Schulferien fest. nextime zeigt dort nur die Feiertage.')
    await click(all('holidays-region')[0])
    await click(one('holidays-go'))
    expect(posted('/api/sources/holidays')[0].body).toEqual({ country: 'CH', regions: ['ZH'], school: false })
  })

  it('TIME-17 AC-6 AC-8 one row per country with "Hintergrund"; "Ändern" opens with the choice before', async () => {
    const holidays = (id: number, country: string, regions: string[], school: boolean) =>
      row(id, { kind: 'holidays', label: country, holidays: { country, regions, school }, calendars: [{ id: id * 10, name: 'Feiertage', color: 'gray', read_only: true }] })
    server(sources([holidays(7, 'DE', ['NW', 'BY'], true), holidays(8, 'AT', ['9'], false)]))
    await open('/settings/calendars')
    expect(all('source-kind').map((kind) => kind.textContent)).toEqual(['Feiertage', 'Feiertage'])
    expect(all('source-line').map((line) => line.textContent)).toEqual(['Deutschland · Nordrhein-Westfalen, Bayern, mit Schulferien', 'Österreich · Wien'])
    expect(all('source-state').map((state) => state.textContent)).toEqual(['Hintergrund', 'Hintergrund'])
    expect(all('source-change').map((button) => button.textContent)).toEqual(['Ändern', 'Ändern'])
    expect(all('source-remove').map((button) => button.textContent)).toEqual(['Entfernen', 'Entfernen'])
    await click(all('source-change')[0])
    expect((one('holidays-country') as HTMLSelectElement).value).toBe('DE')
    expect(all('holidays-region').filter((region) => region.getAttribute('aria-pressed') === 'true').map((region) => region.textContent)).toEqual(['Bayern', 'Nordrhein-Westfalen'])
    expect(one('holidays-school')!.getAttribute('aria-checked')).toBe('true')
  })

  it('TIME-17 AC-6 subscriptions and holidays stand in the sidebar under "Abos und Hintergrund"', async () => {
    serve(
      signedIn(robin, (_method, path) => {
        if (path.startsWith('/api/calendars'))
          return ok(calendarList([calendar(1, 'Familie', { default: true }), calendar(61, 'Müllabfuhr', { group: 'subscriptions', background: true, read_only: true }), calendar(70, 'Feiertage', { group: 'subscriptions', background: true })]))
        if (path === '/api/sources') return ok(sources([]))
        return undefined
      }),
    )
    await open()
    expect(one('calendar-group-subscriptions')!.querySelector('h3')!.textContent).toBe('Abos und Hintergrund')
    expect([...one('calendar-group-subscriptions')!.querySelectorAll('[data-testid="calendar-name"]')].map((name) => name.textContent)).toEqual(['Müllabfuhr', 'Feiertage'])
  })

  it('the birthdays of nexfolk stand apart under "Aus nexfolk", after the other groups', async () => {
    const nexfolk = { id: 9, kind: 'subscription' as const, label: 'nexfolk', state: 'ok' as const, synced_at: null, connection: 'nexfolk' }
    serve(
      signedIn(robin, (_method, path) => {
        if (path.startsWith('/api/calendars'))
          return ok(
            calendarList([
              calendar(1, 'Familie', { default: true }),
              calendar(61, 'Müllabfuhr', { group: 'subscriptions', read_only: true }),
              calendar(80, 'Geburtstage', { group: 'subscriptions', read_only: true, origin: nexfolk }),
            ]),
          )
        if (path === '/api/sources') return ok(sources([]))
        return undefined
      }),
    )
    await open()
    const names = (group: string) => [...one(`calendar-group-${group}`)!.querySelectorAll('[data-testid="calendar-name"]')].map((name) => name.textContent)
    expect(names('subscriptions')).toEqual(['Müllabfuhr'])
    expect(one('calendar-group-nexfolk')!.querySelector('h3')!.textContent).toBe('Aus nexfolk')
    expect(names('nexfolk')).toEqual(['Geburtstage'])
    const heads = [...one('calendar-list')!.querySelectorAll('[data-testid^="calendar-group-"]')].map((group) => group.getAttribute('data-testid'))
    expect(heads.at(-1)).toBe('calendar-group-nexfolk')
  })
})

describe('TIME-17 "Tage hervorheben"', () => {
  const holidays = calendar(2, 'Feiertage Deutschland', {
    background: true,
    holiday: true,
    group: 'subscriptions',
    writable: false,
    origin: { id: 7, kind: 'holidays', label: 'Feiertage', state: 'ok', synced_at: null },
  })
  const listed = (entries = [calendar(1, 'Familie', { default: true }), holidays]) => (_method: string, path: string) =>
    path.startsWith('/api/calendars') ? ok(calendarList(entries)) : undefined

  async function edit(index: number): Promise<HTMLElement> {
    await open()
    await click(one('calendar-list')!.querySelectorAll('[aria-haspopup="menu"]')[index])
    await click(byText('Bearbeiten …'))
    return one('calendar-dialog')!
  }
  const backgroundBox = (dialog: HTMLElement) => dialog.querySelector<HTMLInputElement>('input[aria-label="Als Hintergrund zeigen"]')!

  it('TIME-17 the box stands only under a ticked "Als Hintergrund zeigen" and sends the choice', async () => {
    server(sources([]), listed())
    const dialog = await edit(1)
    const highlight = () => dialog.querySelector<HTMLInputElement>('[data-testid="calendar-highlight"]')
    expect(highlight()?.checked).toBe(true)
    expect(dialog.textContent).toContain('Tage hervorheben')
    await click(backgroundBox(dialog))
    expect(highlight()).toBeNull()
    await click(backgroundBox(dialog))
    await click(highlight())
    await click(one('calendar-save'))
    expect(posted('/api/calendars/2')[0]?.body).toMatchObject({ background: true, holiday: false })
  })

  it('TIME-17 a calendar left alone sends no choice, so it keeps following what holds from the start', async () => {
    server(sources([]), listed())
    const dialog = await edit(0)
    expect(dialog.querySelector('[data-testid="calendar-highlight"]')).toBeNull()
    await click(backgroundBox(dialog))
    expect(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-highlight"]')?.checked).toBe(false)
    await click(one('calendar-save'))
    const body = posted('/api/calendars/1')[0]?.body as Record<string, unknown>
    expect(body).toMatchObject({ background: true })
    expect('holiday' in body).toBe(false)
  })
})
