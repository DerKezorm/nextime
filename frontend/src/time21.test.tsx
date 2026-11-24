/** TIME-21 in the interface: the settings of a calendar with the switch, the devices with notifications, "Mitteilungen
 * erlauben", what keeps notifications from arriving, the preview; and a tapped notification opening its day and event. */
import { Route, Routes } from 'react-router-dom'

import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { NotifySettings } from './features/notify/api'
import { forgetWorker } from './features/notify/browser'
import { CalendarSettingsPage } from './features/notify/CalendarSettings'
import i18n from './i18n'
import { forgetPrefs, prefKey } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, me, ok, render, serve, settle, signedIn, start, stop } from './testing'

const tester = me({ id: 1, name: 'tester', display_name: 'Tester', role: 'operator' })

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-26T15:05:00Z'))
  start()
  forgetWorker()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
  delete (navigator as { serviceWorker?: unknown }).serviceWorker
  forgetWorker()
})

function settings(change: Partial<NotifySettings> = {}): NotifySettings {
  return {
    calendar: { id: 5, name: 'Familie', color: 'green', right: 'owner', owner: null, kind: 'own' },
    shared: true,
    on: false,
    devices: { browsers: [], this_browser: false },
    delivery: { https: true, internet: true },
    preview: {
      bundle: { title: 'Familie', text: 'Anna hat „Elternsprechtag“ am Di, 1.12. um 17:00 eingetragen und „Schwimmen“ auf 11:30 verschoben.' },
      busy: { title: 'Arbeit (von Anna, nur belegt)', text: 'Neuer belegter Zeitraum am Do, 3.12.' },
    },
    ...change,
  }
}

/** A browser that can take notifications: a service worker, push, and the permission `permission` (after asking:
 * `answer`). */
function pushBrowser(permission: NotificationPermission, answer: NotificationPermission = 'granted') {
  const subscription = { endpoint: 'https://push.example.com/abc', options: {}, toJSON: () => ({ keys: { p256dh: 'p256', auth: 'auth' } }), unsubscribe: vi.fn() }
  const registration = { pushManager: { getSubscription: vi.fn(async () => null), subscribe: vi.fn(async () => subscription) } }
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: { register: vi.fn(async () => registration), getRegistration: vi.fn(async () => registration), addEventListener: vi.fn(), removeEventListener: vi.fn() },
  })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', { permission, requestPermission: vi.fn(async () => answer) })
  return registration
}

async function page(first: NotifySettings, subscribed = false, lists = calendarList([calendar(5, 'Familie', { shared: true })])) {
  let state = first
  serve((method, path, body) => {
    if (path === '/api/calendars/5/notify' && method === 'PUT') {
      state = { ...state, on: (body as { on: boolean }).on }
      return ok(state)
    }
    if (path === '/api/calendars/5/notify') return ok(state)
    if (path === '/api/notify/webpush' && method === 'GET') return ok({ key: 'BAAA', subscribed })
    if (path === '/api/notify/webpush' && method === 'PUT') return ok({})
    if (path === '/api/calendars') return ok(lists)
    return undefined
  })
  await render(
    <Routes>
      <Route path="calendar/:id" element={<CalendarSettingsPage />} />
    </Routes>,
    '/calendar/5',
  )
}

describe('TIME-21 the settings of a calendar', () => {
  it('TIME-21 AC-1 the switch is off from the start, with its sentence, and switches per calendar', async () => {
    await page(settings())
    expect(box.querySelector('h1')!.textContent).toBe('Familie · Einstellungen')
    const toggle = box.querySelector<HTMLInputElement>('[data-testid="notify-switch"]')!
    expect(toggle.type).toBe('checkbox')
    expect(toggle.getAttribute('aria-label')).toBe('Mitteilung, wenn andere hier etwas ändern')
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(box.querySelector('[data-testid="notify-card"]')!.textContent).toContain(
      'Ab Werk aus. Mehrere Änderungen kurz hintereinander kommen gebündelt, nach 2 Minuten Ruhe. Deine eigenen Änderungen melden nichts.',
    )
    await click(toggle)
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/calendars/5/notify')?.body).toEqual({ on: true })
    expect(toggle.getAttribute('aria-checked')).toBe('true')
  })

  it('TIME-21 AC-1 a calendar nobody else sees has no switch', async () => {
    await page(settings({ shared: false }))
    expect(box.querySelector('[data-testid="notify-switch"]')).toBeNull()
    expect(box.querySelector('[data-testid="notify-not-shared"]')).not.toBeNull()
  })

  it('TIME-21 AC-5 lists the member\'s devices as allowed and offers "Mitteilungen erlauben" for this browser', async () => {
    const registration = pushBrowser('default')
    await page(
      settings({
        devices: {
          browsers: [
            { id: 1, browser: 'Chrome', system: 'Windows', installed: false, this: false },
            { id: 2, browser: 'Safari', system: 'iOS', installed: true, this: false },
          ],
          this_browser: false,
        },
      }),
    )
    const devices = box.querySelector('[data-testid="notify-devices"]')!
    expect(devices.textContent).toContain('Geräte mit Mitteilungen')
    expect(devices.textContent).toContain('Chrome auf Windows')
    expect(devices.textContent).toContain('nextime auf iOS (Web-App)')
    expect(devices.textContent).toContain('erlaubt')
    const allow = box.querySelector('[data-testid="notify-allow"]')!
    expect(allow.textContent).toBe('Mitteilungen erlauben')
    await click(allow)
    expect(registration.pushManager.subscribe).toHaveBeenCalled()
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/notify/webpush')?.body).toEqual({
      endpoint: 'https://push.example.com/abc',
      keys: { p256dh: 'p256', auth: 'auth' },
      installed: false,
      reminders: true,
      platform: 'other',
    })
    expect(box.querySelector('[data-testid="notify-this-browser"]')!.getAttribute('data-state')).toBe('on')
  })

  it('TIME-21 AC-6 shows how a notification looks: a bundle and one of a "busy only" calendar', async () => {
    await page(settings())
    const preview = box.querySelector('[data-testid="notify-preview"]')!
    expect(preview.textContent).toContain('So sieht die Mitteilung aus')
    const cards = [...preview.querySelectorAll('[data-testid="notify-preview-card"]')].map((card) => card.textContent)
    expect(cards).toEqual([
      'FamilieAnna hat „Elternsprechtag“ am Di, 1.12. um 17:00 eingetragen und „Schwimmen“ auf 11:30 verschoben.',
      'Arbeit (von Anna, nur belegt)Neuer belegter Zeitraum am Do, 3.12.',
    ])
  })

  it('TIME-21 EC-8 switched on with no device allowed, it says nothing arrives and offers to allow', async () => {
    pushBrowser('default')
    await page(settings({ on: true }))
    expect(box.querySelector('[data-testid="notify-nowhere"]')!.textContent).toBe('So kommt nichts an: Noch kein Gerät hat Mitteilungen erlaubt.')
    expect(box.querySelector('[data-testid="notify-allow"]')).not.toBeNull()
  })

  it('TIME-21 EC-9 a browser that refuses notifications explains where to allow them again', async () => {
    pushBrowser('default', 'denied')
    await page(settings())
    await click(box.querySelector('[data-testid="notify-allow"]'))
    expect(box.querySelector('[data-testid="notify-this-browser"]')!.getAttribute('data-state')).toBe('denied')
    expect(box.querySelector('[data-testid="notify-denied-help"]')!.textContent).toContain('Im Schloss-Symbol links neben der Adresse')
    expect(calls.some((call) => call.method === 'PUT' && call.path === '/api/notify/webpush')).toBe(false)
  })

  it('TIME-21 EC-14 without HTTPS or internet it says that nothing arrives', async () => {
    await page(settings({ delivery: { https: false, internet: false } }))
    expect(box.querySelector('[data-testid="notify-no-https"]')!.textContent).toContain('nicht über HTTPS erreichbar')
    expect(box.querySelector('[data-testid="notify-no-internet"]')!.textContent).toContain('erreicht das Internet nicht')
  })

  it('TIME-21 EC-14 a browser that cannot take notifications (Safari outside the web app) says how', async () => {
    await page(settings())
    expect(box.querySelector('[data-testid="notify-this-browser"]')!.getAttribute('data-state')).toBe('unsupported')
    expect(box.textContent).toContain('nur aus der installierten Web-App')
  })
})

describe('TIME-21 AC-4 a tapped notification', () => {
  it('TIME-21 AC-4 opens the day and the event of its address', async () => {
    serve(
      signedIn(tester, (_method, path) => {
        if (path === '/api/calendars') return ok(calendarList([calendar(5, 'Familie')]))
        if (path.startsWith('/api/events/12')) return ok(eventDetail({ id: 12, calendar_id: 5, title: 'Elternabend', start: '2026-12-03T18:00', end: '2026-12-03T19:00' }))
        return undefined
      }),
    )
    await render(<AuthProvider><App /></AuthProvider>, '/day?day=2026-12-03&event=12')
    // Wait for the answers: on a slow runner the event is not there yet right after the first render.
    await settle()
    expect(calls.some((call) => call.path.startsWith('/api/events/12'))).toBe(true)
    // The day of the notification is shown (its window asked for), and the event is open in the side panel.
    // Its window holds noon of that day, whatever time zone the machine running the test has.
    const noon = new Date(2026, 11, 3, 12).getTime()
    const holds = (path: string) => {
      const query = new URLSearchParams(path.split('?')[1])
      return Date.parse(query.get('start') ?? '') <= noon && noon < Date.parse(query.get('end') ?? '')
    }
    expect(calls.some((call) => call.path.startsWith('/api/events?') && holds(call.path))).toBe(true)
    expect([...document.querySelectorAll<HTMLInputElement>('input')].some((field) => field.value === 'Elternabend')).toBe(true)
    // The address loses its marks, so a reload does not open it again.
    expect(calls.filter((call) => call.path.startsWith('/api/events/12')).length).toBe(1)
  })
})

describe('TIME-21 AC-4 the view behind an opened address shows the event\'s calendar', () => {
  beforeEach(() => forgetPrefs())
  afterEach(() => {
    forgetPrefs()
    try {
      localStorage.clear()
    } catch {
      // nothing kept
    }
  })

  const birthday = eventDetail({ id: 12, calendar_id: 5, title: 'Geburtstag Oma', all_day: true, start: '2026-12-03', end: '2026-12-03', recurring: true, occurrence: '20261203' })

  async function openAddress(lists: ReturnType<typeof calendarList>, collections: unknown[] = [], at = '/day?day=2026-12-03&event=12&occurrence=20261203') {
    serve(
      signedIn(tester, (_method, path) => {
        if (path === '/api/calendars') return ok(lists)
        if (path === '/api/collections') return ok({ collections })
        if (path.startsWith('/api/events/12')) return ok(birthday)
        return undefined
      }),
    )
    await render(<AuthProvider><App /></AuthProvider>, at)
    await settle()
  }
  const windows = () => calls.filter((call) => call.path.startsWith('/api/events?')).map((call) => new URLSearchParams(call.path.split('?')[1]))
  const last = () => windows().at(-1)!

  it('TIME-21 AC-4 another calendar shown alone gives way to the event\'s calendar', async () => {
    localStorage.setItem(prefKey('only', 1), '7')
    await openAddress(calendarList([calendar(5, 'Geburtstage'), calendar(7, 'Arbeit')]))
    expect(localStorage.getItem(prefKey('only', 1))).toBe('5')
    expect(last().get('calendars')).toBe('5')
    expect(box.querySelector('[data-testid="only-bar"]')!.textContent).toContain('Geburtstage')
    // The occurrence of the yearly series is what the panel asks for.
    expect(calls.some((call) => call.path.startsWith('/api/events/12') && call.path.includes('occurrence=20261203'))).toBe(true)
  })

  it('TIME-21 AC-4 an event of a hidden calendar shows that calendar alone, hidden ones included', async () => {
    await openAddress(calendarList([calendar(5, 'Geburtstage', { hidden: true }), calendar(7, 'Arbeit', { default: true })]))
    expect(last().get('calendars')).toBe('5')
    expect(last().get('hidden')).toBe('true')
  })

  it('TIME-21 AC-4 a collection without the event\'s calendar gives way, and stays chosen for afterwards', async () => {
    localStorage.setItem(prefKey('collection', 1), '3')
    await openAddress(calendarList([calendar(5, 'Geburtstage'), calendar(7, 'Arbeit')]), [{ id: 3, name: 'Job', calendar_ids: [7], updated_at: '2026-11-20T10:00:00Z' }])
    expect(last().get('calendars')).toBe('5')
    expect(localStorage.getItem(prefKey('collection', 1))).toBe('3')
    await click(box.querySelector('[data-testid="only-end"]'))
    expect(last().get('calendars')).toBe('7')
  })

  it('TIME-21 AC-4 a calendar shown already leaves the choice as it is', async () => {
    localStorage.setItem(prefKey('collection', 1), '3')
    await openAddress(calendarList([calendar(5, 'Geburtstage'), calendar(7, 'Arbeit')]), [{ id: 3, name: 'Familie', calendar_ids: [5, 7], updated_at: '2026-11-20T10:00:00Z' }])
    expect(localStorage.getItem(prefKey('only', 1))).toBeNull()
    expect(last().get('calendars')).toBe('5,7')
    expect(box.querySelector('[data-testid="only-bar"]')).toBeNull()
  })
})

describe('TIME-4 defaults on the page of a shared calendar', () => {
  it('TIME-4 defaults offered for a shared calendar one may write in, not for one only to be read', async () => {
    const shared = { group: 'shared' as const, owner: 'Anna' }
    await page(settings(), false, calendarList([calendar(5, 'Familie', { ...shared, right: 'edit', writable: true })]))
    expect(box.querySelector('[data-testid="calendar-settings-defaults"]')).not.toBeNull()
    stop()
    start()
    await page(settings(), false, calendarList([calendar(5, 'Familie', { ...shared, right: 'read', writable: false })]))
    expect(box.querySelector('[data-testid="calendar-settings-defaults"]')).toBeNull()
  })
})
