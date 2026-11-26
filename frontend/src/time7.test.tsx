/** TIME-7 in the interface: the row "Erinnerungen" of an event (fixed and free lead times, × to remove, whole days,
 * the calendar's default for a new event, one's own with the right to read), the area "Erinnerungen" under My
 * account (both switches, the defaults per calendar, the devices, the preview, what keeps reminders from arriving),
 * a tapped reminder of a series opening its occurrence, and "In 5 Min erneut" in the service worker. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { EventDetail } from './features/calendars/api'
import { forgetWorker, platform } from './features/notify/browser'
import type { ReminderSettings } from './features/reminders/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, choose, click, me, ok, render, serve, settle, signedIn, start, stop } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })

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
  try {
    localStorage.clear()
  } catch {
    // nothing kept
  }
})

type Handler = (method: string, path: string, body: unknown) => { status: number; body: unknown } | undefined

function server(detail: EventDetail | null, more: Handler = () => undefined, lists = calendarList([calendar(1, 'Persönlich', { default: true }), calendar(2, 'Familie', { color: 'green', reminder: 30 })])) {
  let current = detail
  serve(
    signedIn(anna, (method, path, body) => {
      const extra = more(method, path, body)
      if (extra) return extra
      if (path.startsWith('/api/calendars')) return ok(lists)
      if (path.startsWith('/api/events?') && current)
        return ok([{ id: current.id, occurrence: current.occurrence, calendar_id: current.calendar_id, title: current.title, busy: true, busy_only: current.busy_only, recurring: current.recurring, all_day: false, start: '2026-11-26T17:30:00Z', end: '2026-11-26T19:00:00Z', time_zone: 'Europe/Berlin' }])
      if (current && path === `/api/events/${current.id}/reminders` && method === 'PUT') {
        const minutes = (body as { minutes: number[] }).minutes
        current = { ...current, reminders: { ...current.reminders!, minutes, own: true } }
        return ok(current)
      }
      if (current && path.startsWith(`/api/events/${current.id}`) && method === 'GET') return ok(current)
      if (path === '/api/events' && method === 'POST') return { status: 201, body: eventDetail() }
      return undefined
    }),
  )
}

async function open(at = '/'): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
}

const field = <T extends HTMLElement>(id: string) => document.querySelector<T>(`[data-testid="${id}"]`)!
const chips = () => [...document.querySelectorAll('[data-testid="reminder-chip"]')].map((chip) => chip.textContent!.replace('×', '').trim())

function withReminders(minutes: number[], change: Partial<EventDetail> = {}): EventDetail {
  return eventDetail({ reminders: { minutes, own: true, default: [], presets: change.all_day ? [360, -540] : [5, 15, 30, 60, 1440] }, ...change })
}

async function openEvent(): Promise<void> {
  await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
}

async function free(amount: string, unit: string): Promise<void> {
  await click(field('reminder-add'))
  await click(field('reminder-free'))
  const input = field<HTMLInputElement>('reminder-free-amount')
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, amount)
  act(() => void input.dispatchEvent(new Event('input', { bubbles: true })))
  await choose(field<HTMLSelectElement>('reminder-free-unit'), unit)
  await click(field('reminder-free-take'))
}

describe('TIME-7 the row "Erinnerungen" of an event', () => {
  it('TIME-7 AC-2 several fixed and free lead times, each removable with ×, saved as the member\'s own', async () => {
    server(withReminders([15]))
    await open()
    await openEvent()
    expect(chips()).toEqual(['15 Min vorher'])
    await click(field('reminder-add'))
    const offered = [...document.querySelectorAll('[data-testid="reminder-option"]')].map((option) => option.textContent)
    expect(offered).toEqual(['5 Min vorher', '30 Min vorher', '1 Stunde vorher', '1 Tag vorher'])
    expect(field('reminder-free').textContent).toBe('Frei wählen …')
    await click(byText('1 Stunde vorher'))
    expect(chips()).toEqual(['1 Stunde vorher', '15 Min vorher'])
    await click(document.querySelector('[aria-label="Erinnerung 15 Min vorher entfernen"]'))
    expect(chips()).toEqual(['1 Stunde vorher'])
    await free('2', '1440')
    expect(chips()).toEqual(['2 Tage vorher', '1 Stunde vorher'])
    await click(field('panel-save'))
    expect(calls.filter((call) => call.method === 'PUT' && call.path === '/api/events/7/reminders').map((call) => call.body)).toEqual([{ minutes: [2880, 60] }])
    // Only the reminders changed: nothing of the event itself goes (no new version for the others).
    expect(calls.some((call) => call.method === 'PATCH')).toBe(false)
  })

  it('TIME-7 AC-2 a whole day offers "am Vortag um 18:00" and "am Tag um 09:00"', async () => {
    server(withReminders([360], { all_day: true, start: '2026-11-27', end: '2026-11-27' }))
    await open()
    await openEvent()
    expect(chips()).toEqual(['am Vortag um 18:00'])
    await click(field('reminder-add'))
    expect([...document.querySelectorAll('[data-testid="reminder-option"]')].map((option) => option.textContent)).toEqual(['am Tag um 09:00'])
  })

  it('TIME-7 EC-8 a free lead time that is none is refused with a sentence, and the same one twice is there once', async () => {
    server(withReminders([120]))
    await open()
    await openEvent()
    for (const wrong of ['', '-5', 'abc', '0', '1,5']) {
      await free(wrong, '60')
      expect(field('reminder-free-problem').textContent).toBe('Bitte eine ganze Zahl größer als 0.')
      await click(byText('Abbrechen', field('reminder-free-dialog')))
    }
    await free('29', '1440')
    expect(field('reminder-free-problem').textContent).toBe('Höchstens 4 Wochen vorher.')
    await click(byText('Abbrechen', field('reminder-free-dialog')))
    await free('2', '60')
    expect(chips()).toEqual(['2 Stunden vorher'])
  })

  it('TIME-7 AC-3 a new event shows its calendar\'s default and sends none of its own unless changed', async () => {
    server(null, () => undefined, calendarList([calendar(2, 'Familie', { color: 'green', reminder: 30, default: true })]))
    await open()
    await click(box.querySelector('[data-testid="new-event"]'))
    expect(chips()).toEqual(['30 Min vorher'])
    expect(field('reminders').textContent).toContain('wie die Vorgabe des Kalenders')
    const title = field<HTMLInputElement>('field-title')
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'Training')
    act(() => void title.dispatchEvent(new Event('input', { bubbles: true })))
    await click(field('panel-save'))
    const made = calls.find((call) => call.method === 'POST' && call.path === '/api/events')!.body as Record<string, unknown>
    expect('reminders' in made).toBe(false)
  })

  it('TIME-7 AC-3 a new event whose reminders the member changed sends them', async () => {
    server(null, () => undefined, calendarList([calendar(2, 'Familie', { color: 'green', reminder: 30, default: true })]))
    await open()
    await click(box.querySelector('[data-testid="new-event"]'))
    await click(document.querySelector('[aria-label="Erinnerung 30 Min vorher entfernen"]'))
    await click(field('reminder-add'))
    await click(byText('5 Min vorher'))
    const title = field<HTMLInputElement>('field-title')
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'Training')
    act(() => void title.dispatchEvent(new Event('input', { bubbles: true })))
    await click(field('panel-save'))
    expect((calls.find((call) => call.method === 'POST' && call.path === '/api/events')!.body as { reminders: number[] }).reminders).toEqual([5])
  })

  it('TIME-7 AC-9 with the right to read the member\'s own reminders change at once', async () => {
    server(withReminders([], { writable: false, right: 'read', shared: true, calendar_id: 2 }))
    await open()
    await openEvent()
    expect(field('read-only')).not.toBeNull()
    await click(field('reminder-add'))
    await click(byText('15 Min vorher'))
    expect(calls.filter((call) => call.path === '/api/events/7/reminders').map((call) => call.body)).toEqual([{ minutes: [15] }])
    expect(chips()).toEqual(['15 Min vorher'])
    expect(field('reminders').textContent).toContain('Deine Erinnerungen gelten nur für dich.')
  })

  it('TIME-7 EC-10 single events of a series with reminders of their own from a device are named and stay', async () => {
    const series = withReminders([15], { writable: false, right: 'read', recurring: true })
    server({ ...series, reminders: { ...series.reminders!, occurrences: ['20261127T170000Z'] } })
    await open()
    await openEvent()
    expect(field('reminders').textContent).toContain('Einzelne Termine dieser Serie haben eigene Erinnerungen von einem Gerät; die bleiben.')
    await click(field('reminder-add'))
    await click(byText('30 Min vorher'))
    expect(chips()).toEqual(['30 Min vorher', '15 Min vorher'])
    expect(field('reminders').textContent).toContain('die bleiben.')
  })

  it('TIME-7 EC-10 a series without such single events says nothing of them', async () => {
    server(withReminders([15], { writable: false, right: 'read', recurring: true }))
    await open()
    await openEvent()
    expect(field('reminders').textContent).not.toContain('Einzelne Termine')
  })

  it('TIME-7 AC-4 a tapped reminder of a series opens its day and occurrence', async () => {
    server(withReminders([15], { recurring: true, occurrence: '20261126T173000Z' }))
    await open('/day?day=2026-11-26&event=7&occurrence=20261126T173000Z')
    await settle()
    expect(calls.some((call) => call.method === 'GET' && call.path.startsWith('/api/events/7') && call.path.includes('occurrence=20261126T173000Z'))).toBe(true)
    expect(document.querySelector('[data-testid="event-panel"]')).not.toBeNull()
  })
})

function area(change: Partial<ReminderSettings> = {}): ReminderSettings {
  return {
    not_double: true,
    apple: [],
    this_browser: { subscribed: true, reminders: true, quiet: false },
    browsers: [
      { id: 1, browser: 'Chrome', system: 'Windows', installed: false, this: true, reminders: true, quiet: false },
      { id: 2, browser: 'Safari', system: 'iOS', installed: true, this: false, reminders: true, quiet: true },
    ],
    calendars: [
      { id: 1, name: 'Familie', color: 'green', owner: null, kind: 'own', hidden: false, choice: '30', options: ['none', '5', '15', '30', '60', '1440'] },
      { id: 2, name: 'Arbeit', color: 'blue', owner: null, kind: 'icloud', hidden: false, choice: 'source', options: ['source', 'none', '5', '15', '30', '60', '1440'] },
      { id: 3, name: 'Verein', color: 'teal', owner: 'Ben', kind: 'own', hidden: false, choice: 'none', options: ['none', '5', '15', '30', '60', '1440'] },
    ],
    delivery: { https: true, internet: true },
    preview: { title: 'Zahnarzt Ben · in 15 Minuten', text: '16:30–17:15 · Praxis Dr. Weber, Lindenstraße 4', snooze: 'In 5 Min erneut', open: 'Öffnen' },
    ...change,
  }
}

/** A browser that can take notifications with `permission` (after asking: `answer`). */
function pushBrowser(permission: NotificationPermission, answer: NotificationPermission = 'granted', subscribed = true) {
  const subscription = { endpoint: 'https://push.example.com/abc', options: {}, toJSON: () => ({ keys: { p256dh: 'p256', auth: 'auth' } }), unsubscribe: vi.fn() }
  const registration = { pushManager: { getSubscription: vi.fn(async () => (subscribed ? subscription : null)), subscribe: vi.fn(async () => subscription) } }
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: { register: vi.fn(async () => registration), getRegistration: vi.fn(async () => registration), addEventListener: vi.fn(), removeEventListener: vi.fn() },
  })
  vi.stubGlobal('PushManager', class {})
  vi.stubGlobal('Notification', { permission, requestPermission: vi.fn(async () => answer) })
}

function areaServer(first: ReminderSettings, subscribed = true) {
  let state = first
  serve(
    signedIn(anna, (method, path, body) => {
      if (path === '/api/reminders' && method === 'PUT') return ok((state = { ...state, not_double: (body as { not_double: boolean }).not_double }))
      if (path.startsWith('/api/reminders/calendars/') && method === 'PUT') {
        const id = Number(path.split('/').pop())
        state = { ...state, calendars: state.calendars.map((each) => (each.id === id ? { ...each, choice: (body as { choice: string }).choice } : each)) }
        return ok(state)
      }
      if (path === '/api/notify/webpush/reminders') return ok((state = { ...state, this_browser: { ...state.this_browser, reminders: (body as { on: boolean }).on } }))
      if (path === '/api/reminders') return ok(state)
      if (path === '/api/notify/webpush' && method === 'GET') return ok({ key: 'BAAA', subscribed, reminders: true })
      if (path === '/api/notify/webpush' && method === 'PUT') return ok({})
      if (path.startsWith('/api/calendars')) return ok(calendarList([calendar(1, 'Familie')]))
      return undefined
    }),
  )
}

/** A row with a tick of the ui kit (`CheckboxRow`): its box, found by exactly its label. */
const toggle = (label: string) => {
  const named = [...document.querySelectorAll<HTMLLabelElement>('label[for]')].find((element) => element.textContent === label)
  return (named ? document.getElementById(named.htmlFor) : null) as HTMLInputElement
}

describe('TIME-7 the area "Erinnerungen"', () => {
  it('TIME-7 AC-8 both switches, the default per calendar with color, name and choice, and the preview', async () => {
    pushBrowser('granted')
    areaServer(area())
    await open('/settings/reminders')
    expect(field('reminders-area')).not.toBeNull()
    expect(toggle('Auf diesem Gerät erinnern').checked).toBe(true)
    expect(toggle('Nicht doppelt, wenn Apple Kalender schon erinnert').checked).toBe(true)
    expect(field('reminders-area').textContent).toContain('Auf einem iPhone mit eingerichtetem Kalenderkonto erinnert Apple Kalender selbst; nextime schweigt dort.')
    const rows = [...document.querySelectorAll('[data-testid="reminder-default"]')]
    expect(rows.map((row) => row.textContent!.split('Keine')[0].split('5 Min')[0])).toHaveLength(3)
    expect(rows[2].textContent).toContain('Verein · Ben')
    expect(rows.map((row) => row.querySelector('[data-color]')!.getAttribute('data-color'))).toEqual(['green', 'blue', 'teal'])
    const arbeit = field<HTMLSelectElement>('reminder-default-2')
    expect(arbeit.value).toBe('source')
    expect([...arbeit.options].map((option) => option.textContent)).toEqual(['wie in iCloud', 'Keine', '5 Min vorher', '15 Min vorher', '30 Min vorher', '1 Stunde vorher', '1 Tag vorher'])
    await choose(field<HTMLSelectElement>('reminder-default-1'), '15')
    expect(calls.find((call) => call.path === '/api/reminders/calendars/1')!.body).toEqual({ choice: '15' })
    expect(field<HTMLSelectElement>('reminder-default-1').value).toBe('15')
    expect(field('reminder-preview-title').textContent).toBe('Zahnarzt Ben · in 15 Minuten')
    expect(field('reminder-preview-text').textContent).toBe('16:30–17:15 · Praxis Dr. Weber, Lindenstraße 4')
    expect(field('reminder-preview').textContent).toContain('In 5 Min erneut')
    expect(field('reminder-preview').textContent).toContain('Öffnen')
    // The preview sits inside the reminders card, not in a column of its own beside the table.
    expect(field('reminders-area').className).not.toMatch(/grid-cols-/)
    expect(field('reminder-preview').parentElement).not.toBe(field('reminders-area'))
    const devices = field('reminder-devices').textContent!
    expect(devices).toContain('Dieser Browser')
    expect(devices).toContain('schweigt, Apple Kalender erinnert')
    await click(toggle('Nicht doppelt, wenn Apple Kalender schon erinnert'))
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/reminders')!.body).toEqual({ not_double: false })
  })

  it('TIME-7 AC-6 "Auf diesem Gerät erinnern" switches this browser only and is kept for its next sign-up', async () => {
    pushBrowser('granted')
    areaServer(area())
    await open('/settings/reminders')
    await click(toggle('Auf diesem Gerät erinnern'))
    expect(calls.find((call) => call.path === '/api/notify/webpush/reminders')!.body).toEqual({ on: false })
    expect(toggle('Auf diesem Gerät erinnern').checked).toBe(false)
    expect(localStorage.getItem('nextime.reminders.here')).toBe('off')
  })

  it('TIME-7 EC-4 a browser never asked offers "Mitteilungen erlauben" first, then the browser asks', async () => {
    pushBrowser('default', 'granted', false)
    areaServer(area({ this_browser: { subscribed: false, reminders: true, quiet: false } }), false)
    await open('/settings/reminders')
    expect(toggle('Auf diesem Gerät erinnern').disabled).toBe(true)
    expect(field('reminders-allow-hint').textContent).toContain('Dieser Browser zeigt noch keine Mitteilungen von nextime.')
    // "Auf welchem Weg" of the ui kit (plan "Einstellungen", E1): "Mitteilungen einschalten" for this browser.
    await click(field('notify-browser-on'))
    expect((globalThis as unknown as { Notification: { requestPermission: () => void } }).Notification.requestPermission).toHaveBeenCalled()
    const sent = calls.find((call) => call.method === 'PUT' && call.path === '/api/notify/webpush')!.body as Record<string, unknown>
    expect(sent.reminders).toBe(true)
    expect(sent.platform).toBe('other')
  })

  it('TIME-7 EC-2 a browser that blocks notifications says where to allow them', async () => {
    pushBrowser('denied')
    areaServer(area({ this_browser: { subscribed: false, reminders: true, quiet: false } }), false)
    await open('/settings/reminders')
    expect(field('reminders-denied').textContent).toContain('Dieser Browser erlaubt keine Mitteilungen. Im Schloss-Symbol links neben der Adresse')
  })

  it('TIME-7 EC-5 Safari on the iPhone without the web app says that reminders come only from the installed web app', async () => {
    areaServer(area({ this_browser: { subscribed: false, reminders: true, quiet: false } }), false)
    await open('/settings/reminders')
    expect(field('reminders-unsupported').textContent).toContain('nur aus der installierten Web-App')
  })

  it('TIME-7 EC-15 without HTTPS, internet or a device the area says nothing arrives', async () => {
    pushBrowser('granted')
    areaServer(area({ browsers: [], delivery: { https: false, internet: false } }))
    await open('/settings/reminders')
    expect(field('reminders-no-https').textContent).toContain('nicht über HTTPS erreichbar')
    expect(field('reminders-no-internet').textContent).toContain('erreicht das Internet nicht')
    expect(field('reminders-no-device').textContent).toBe('So kommt nichts an: Noch kein Gerät erinnert.')
  })

  it('TIME-7 EC-16 the area in English', async () => {
    await i18n.changeLanguage('en')
    pushBrowser('granted')
    areaServer(area())
    await open('/settings/reminders')
    expect(toggle('Remind me on this device')).not.toBeNull()
    expect(toggle('Not twice when Apple Calendar reminds already')).not.toBeNull()
    expect([...field<HTMLSelectElement>('reminder-default-2').options][0].textContent).toBe('as in iCloud')
  })
})

describe('TIME-7 what kind of device a browser runs on', () => {
  function device(agent: string, touch: number): void {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(agent)
    Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, get: () => touch })
  }
  afterEach(() => {
    delete (navigator as { maxTouchPoints?: number }).maxTouchPoints
  })

  it('TIME-7 AC-7 an iPad Safari, which calls itself a Mac, is an iPad; a Mac without touch is a Mac', () => {
    device('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15', 5)
    expect(platform()).toBe('ipad')
    device('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15', 0)
    expect(platform()).toBe('mac')
    device('Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1', 5)
    expect(platform()).toBe('iphone')
    device('Mozilla/5.0 (iPad; CPU OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1', 5)
    expect(platform()).toBe('ipad')
    device('Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0', 0)
    expect(platform()).toBe('other')
  })
})

describe('TIME-7 the service worker', () => {
  type Listener = (event: Record<string, unknown>) => void

  async function worker() {
    const listeners: Record<string, Listener> = {}
    const shown: { title: string; options: Record<string, unknown> }[] = []
    const fetched: { url: string; init: RequestInit }[] = []
    const opened: string[] = []
    const self = {
      addEventListener: (name: string, listener: Listener) => (listeners[name] = listener),
      registration: { showNotification: async (title: string, options: Record<string, unknown>) => void shown.push({ title, options }) },
      clients: { matchAll: async () => [], openWindow: async (url: string) => void opened.push(url), claim: async () => undefined },
      skipWaiting: () => undefined,
      location: { origin: 'https://nextime.example.com' },
    }
    const fs = (await import(/* @vite-ignore */ 'node:' + 'fs')) as { readFileSync: (file: string, encoding: string) => string }
    const code = fs.readFileSync('public/sw.js', 'utf-8')
    const fakeFetch = async (url: string, init: RequestInit) => void fetched.push({ url, init })
    new Function('self', 'fetch', 'Notification', code)(self, fakeFetch, { maxActions: 2 })
    return { listeners, shown, fetched, opened }
  }

  it('TIME-7 AC-4 EC-17 a reminder shows "In 5 Min erneut" and "Öffnen"; the first sends its mark back, the second opens', async () => {
    const sw = await worker()
    let waited: Promise<unknown> = Promise.resolve()
    const waitUntil = (promise: Promise<unknown>) => (waited = promise)
    sw.listeners.push({
      data: { json: () => ({ title: 'Zahnarzt Ben · in 15 Minuten', body: '16:30–17:15', url: '/day?day=2026-11-26&event=7', tag: 'reminder-7-x', snooze: 'mark.sig', actions: [{ action: 'evil', title: 'x' }, { action: 'snooze', title: 'In 5 Min erneut' }, { action: 'open', title: 'Öffnen' }] }) },
      waitUntil,
    })
    await waited
    expect(sw.shown[0].title).toBe('Zahnarzt Ben · in 15 Minuten')
    expect(sw.shown[0].options.actions).toEqual([{ action: 'snooze', title: 'In 5 Min erneut' }, { action: 'open', title: 'Öffnen' }])
    const notification = { close: vi.fn(), data: sw.shown[0].options.data }
    sw.listeners.notificationclick({ action: 'snooze', notification, waitUntil })
    await waited
    expect(sw.fetched).toHaveLength(1)
    expect(sw.fetched[0].url).toBe('/api/reminders/snooze')
    expect(JSON.parse(String(sw.fetched[0].init.body))).toEqual({ mark: 'mark.sig' })
    expect(sw.opened).toEqual([])
    sw.listeners.notificationclick({ action: 'open', notification, waitUntil })
    await waited
    expect(sw.opened).toEqual(['/day?day=2026-11-26&event=7'])
  })
})
