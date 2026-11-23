/** TIME-5 in the interface: the side panel, a new event of an hour, "Busy" with "All day", the checks at the fields,
 * links and "Join", read-only calendars, closing with changes, a conflict, who entered and changed, another zone, a
 * second click and an event deleted meanwhile. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { EventDetail } from './features/calendars/api'
import { joinLink, linkParts, LinkedText } from './features/calendars/links'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, choose, click, me, ok, render, serve, signedIn, start, stop, type } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })
const NOW = new Date('2026-11-26T15:05:00Z')

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

type Handler = (method: string, path: string, body: unknown) => { status: number; body: unknown } | undefined

function server(detail: EventDetail | null = null, more: Handler = () => undefined, lists = calendarList([calendar(1, 'Persönlich', { default: true }), calendar(2, 'Familie', { color: 'green' })])) {
  serve(
    signedIn(anna, (method, path, body) => {
      const extra = more(method, path, body)
      if (extra) return extra
      if (path.startsWith('/api/calendars')) return ok(lists)
      if (path.startsWith('/api/events?') && detail)
        return ok([{ id: detail.id, occurrence: detail.occurrence, calendar_id: detail.calendar_id, title: detail.title, busy: detail.busy, busy_only: detail.busy_only, recurring: detail.recurring, all_day: false, start: '2026-11-26T17:30:00Z', end: '2026-11-26T19:00:00Z', time_zone: 'Europe/Berlin' }])
      if (path.startsWith(`/api/events/${detail?.id}`) && method === 'GET') return ok(detail)
      if (path === '/api/events' && method === 'POST') return { status: 201, body: eventDetail() }
      return undefined
    }),
  )
}

async function open(): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    '/',
  )
}

const panel = () => document.querySelector<HTMLElement>('[data-testid="event-panel"]')
const field = <T extends HTMLElement>(id: string) => document.querySelector<T>(`[data-testid="${id}"]`)!

function fill(element: HTMLTextAreaElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(element, value)
  act(() => void element.dispatchEvent(new Event('input', { bubbles: true })))
}

async function newEvent(): Promise<void> {
  await click(box.querySelector('[data-testid="new-event"]'))
}

async function openEvent(): Promise<void> {
  await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
}

describe('TIME-5 the side panel', () => {
  it('TIME-5 AC-8 EC-14 opens on the right with the head and every field from top to bottom', async () => {
    server(eventDetail({ calendar_id: 2 }))
    await open()
    await openEvent()
    expect(panel()).not.toBeNull()
    expect(field('panel-head').textContent).toBe('Familie')
    const text = panel()!.textContent!
    const order = ['Beginn', 'Ende', 'Ganztägig', 'Belegt', 'Berlin (wie Gerät)', 'Andere Zeitzone', 'Kalender', 'Wiederholung', 'Erinnerungen', 'Ort', 'Notizen und Links', 'Anhänge', 'Löschen', 'Abbrechen', 'Sichern']
    const places = order.map((word) => text.indexOf(word))
    expect(places.every((place) => place >= 0)).toBe(true)
    expect([...places].sort((a, b) => a - b)).toEqual(places)
    const focusable = [...panel()!.querySelectorAll<HTMLElement>('aside input, aside select, aside textarea, aside button')].map((element) => element.dataset.testid ?? element.getAttribute('aria-label') ?? element.textContent)
    expect(focusable.indexOf('field-title')).toBeLessThan(focusable.indexOf('field-start-date'))
    expect(focusable.indexOf('field-start-date')).toBeLessThan(focusable.indexOf('switch-all-day'))
    expect(focusable.indexOf('field-calendar')).toBeLessThan(focusable.indexOf('field-notes'))
    expect(focusable.indexOf('field-notes')).toBeLessThan(focusable.indexOf('panel-save'))
    expect(field<HTMLInputElement>('field-title').value).toBe('Training')
  })

  it('TIME-5 AC-10 AC-1 a new event lasts an hour, lands in the default calendar and is saved', async () => {
    server()
    await open()
    await newEvent()
    expect(field<HTMLInputElement>('field-start-time').value).toBe('17:00')
    expect(field<HTMLInputElement>('field-end-time').value).toBe('18:00')
    expect(field<HTMLSelectElement>('field-calendar').value).toBe('1')
    type(field<HTMLInputElement>('field-title'), '  Elternabend ')
    await click(field('panel-save'))
    const sent = calls.find((call) => call.method === 'POST' && call.path === '/api/events')
    expect(sent?.body).toMatchObject({ title: 'Elternabend', all_day: false, start: '2026-11-26T17:00', end: '2026-11-26T18:00', time_zone: 'Europe/Berlin', busy: true, calendar_id: 1, rule: null })
    expect(panel()).toBeNull()
  })

  it('TIME-5 AC-2 AC-9 EC-12 "Busy" follows "All day" until it is touched, and a whole day has dates only', async () => {
    server()
    await open()
    await newEvent()
    const busy = field('switch-busy')
    expect(busy.getAttribute('aria-checked')).toBe('true')
    await click(field('switch-all-day'))
    expect(busy.getAttribute('aria-checked')).toBe('false')
    expect(document.querySelector('[data-testid="field-start-time"]')).toBeNull()
    await click(field('switch-all-day'))
    expect(field('switch-busy').getAttribute('aria-checked')).toBe('true')
    await click(field('switch-busy'))
    await click(field('switch-all-day'))
    expect(field('switch-busy').getAttribute('aria-checked')).toBe('false')
    await click(field('switch-busy'))
    type(field<HTMLInputElement>('field-title'), 'Klassenfahrt')
    type(field<HTMLInputElement>('field-end-date'), '2026-11-28')
    await click(field('panel-save'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/events')?.body).toMatchObject({ all_day: true, start: '2026-11-26', end: '2026-11-28', time_zone: '', busy: true })
  })

  it('TIME-5 EC-1 EC-3 EC-6 EC-13 problems stand at their field and nothing is saved', async () => {
    server()
    await open()
    await newEvent()
    await click(field('panel-save'))
    expect(panel()!.textContent).toContain('Der Titel fehlt.')
    type(field<HTMLInputElement>('field-title'), 't'.repeat(201))
    fill(field<HTMLTextAreaElement>('field-notes'), 'n'.repeat(10_001))
    type(field<HTMLInputElement>('field-end-time'), '16:30')
    await click(field('panel-save'))
    const text = panel()!.textContent!
    expect(text).toContain('Zu lang: höchstens 200 Zeichen, jetzt 201.')
    expect(text).toContain('Zu lang: höchstens 10.000 Zeichen, jetzt 10.001.')
    expect(text).toContain('Das Ende liegt vor dem Beginn (17:00).')
    type(field<HTMLInputElement>('field-title'), 'x')
    fill(field<HTMLTextAreaElement>('field-notes'), '<b>eingefügt</b>')
    type(field<HTMLInputElement>('field-start-date'), '2026-02-31')
    await click(field('panel-save'))
    expect(panel()!.textContent).toContain('Dieses Datum gibt es nicht.')
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/events')).toBe(false)
    expect(panel()!.querySelector('b')).toBeNull()
  })

  it('TIME-5 AC-3 an event in another zone shows its own time and yours', async () => {
    server()
    await open()
    await newEvent()
    expect(field('zone-name').textContent).toBe('Berlin (wie Gerät)')
    await click(field('other-zone'))
    await choose(field<HTMLSelectElement>('field-zone'), 'America/New_York')
    type(field<HTMLInputElement>('field-start-time'), '09:00')
    type(field<HTMLInputElement>('field-end-time'), '10:00')
    expect(field('zone-hint').textContent).toMatch(/^Eingetragen 09:00 in New York, bei dir .*26\. November 15:00\.$/)
    type(field<HTMLInputElement>('field-title'), 'Flug')
    await click(field('panel-save'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/events')?.body).toMatchObject({ start: '2026-11-26T09:00', time_zone: 'America/New_York' })
  })
})

describe('TIME-5 links and join', () => {
  it('TIME-5 AC-4 each of the six services gets "Join", with two links the first, a look-alike none', () => {
    for (const link of ['https://zoom.us/j/123', 'https://us02web.zoom.us/j/1', 'https://meet.google.com/abc-defg-hij', 'https://teams.microsoft.com/l/meetup-join/x', 'https://firma.webex.com/meet/x', 'https://meet.jit.si/Familie', 'https://facetime.apple.com/join#v=1']) {
      expect(joinLink(`Einwahl: ${link} bitte`)).toBe(link)
    }
    expect(joinLink('erst https://meet.google.com/aaa dann https://zoom.us/j/9')).toBe('https://meet.google.com/aaa')
    expect(joinLink('https://zoom.us.example.com/j/1 und https://example.com/meet.google.com')).toBeNull()
  })

  it('TIME-5 EC-7 only web, mail and phone addresses become links; code and javascript stay text', async () => {
    const parts = linkParts('javascript:alert(1) <script>x()</script> https://example.com/a, mailto:a@example.com tel:+49 30 1234')
    expect(parts.filter((part) => part.href).map((part) => part.href)).toEqual(['https://example.com/a', 'mailto:a@example.com', 'tel:+49301234'])
    server(eventDetail({ notes: 'Einwahl https://meet.google.com/abc <img src=x onerror=alert(1)> javascript:alert(1)' }))
    await open()
    await openEvent()
    const links = [...panel()!.querySelectorAll<HTMLAnchorElement>('[data-testid="notes-links"] a')].map((link) => link.getAttribute('href'))
    expect(links).toEqual(['https://meet.google.com/abc'])
    expect(panel()!.querySelector('img')).toBeNull()
    expect(field<HTMLAnchorElement>('join').getAttribute('href')).toBe('https://meet.google.com/abc')
    expect(field('join').textContent).toBe('Beitreten')
    expect(typeof LinkedText).toBe('function')
  })
})

describe('TIME-5 read only, closing, conflicts, people', () => {
  it('TIME-5 AC-5 a calendar one may only read shows no edit buttons and says why', async () => {
    const lists = calendarList([calendar(1, 'Persönlich'), calendar(3, 'Annas', { group: 'shared', right: 'read', writable: false, owner: 'Anna' })])
    server(eventDetail({ calendar_id: 3, writable: false, right: 'read' }), () => undefined, lists)
    await open()
    await openEvent()
    expect(field('read-only-reason').textContent).toBe('„Annas“ ist nur lesbar.')
    expect(document.querySelector('[data-testid="panel-save"]')).toBeNull()
    expect(document.querySelector('[data-testid="panel-delete"]')).toBeNull()
  })

  it('TIME-5 dialog focus goes into an event only to be read, and back where it was on closing', async () => {
    const lists = calendarList([calendar(1, 'Persönlich'), calendar(3, 'Annas', { group: 'shared', right: 'read', writable: false, owner: 'Anna' })])
    server(eventDetail({ calendar_id: 3, writable: false, right: 'read' }), () => undefined, lists)
    await open()
    const opener = box.querySelector<HTMLElement>('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]')!
    opener.focus()
    await click(opener)
    const shell = field('event-shell')
    expect(shell.contains(document.activeElement)).toBe(true)
    await click(field('panel-close'))
    expect(document.querySelector('[data-testid="event-shell"]')).toBeNull()
    expect(document.activeElement).toBe(opener)
  })

  it('TIME-5 AC-5 busy only shows a block without title and why', async () => {
    const lists = calendarList([calendar(1, 'Persönlich'), calendar(3, 'Annas', { group: 'shared', right: 'busy', writable: false, owner: 'Anna' })])
    server(eventDetail({ calendar_id: 3, writable: false, right: 'busy', busy_only: true, title: '' }), () => undefined, lists)
    await open()
    await openEvent()
    expect(field('read-only').textContent).toContain('Belegt')
    expect(field('read-only-reason').textContent).toBe('Anna teilt diesen Kalender mit dir nur als belegt. Titel, Ort und Notizen bleiben bei Anna.')
  })

  it('TIME-5 AC-14 closing with changes asks first, without changes it just closes', async () => {
    server()
    await open()
    await newEvent()
    await click(field('panel-close'))
    expect(panel()).toBeNull()
    await newEvent()
    type(field<HTMLInputElement>('field-title'), 'Halb fertig')
    await click(field('panel-close'))
    expect(document.querySelector('[role="dialog"]:not([data-event-dialog])')?.getAttribute('aria-label')).toBe('Änderungen verwerfen?')
    await click(byText('Weiter bearbeiten'))
    expect(field<HTMLInputElement>('field-title').value).toBe('Halb fertig')
    await click(field('panel-behind'))
    expect(document.querySelector('[data-testid="discard-question"]')).not.toBeNull()
    await click(byText('Weiter bearbeiten'))
    await click(box.querySelector('a[href="/week"]'))
    expect(document.querySelector('[data-testid="discard-question"]')).not.toBeNull()
    expect(field<HTMLInputElement>('field-title').value).toBe('Halb fertig')
    await click(byText('Verwerfen'))
    expect(panel()).toBeNull()
  })

  it('TIME-5 EC-2 the overwritten one sees both versions, "Meine zurückholen" and "So lassen"', async () => {
    const clash = {
      by: { id: 2, name: 'Anna', color: 1, avatar: null },
      self: false,
      mine: { title: 'Training (Halle 2)', all_day: false, start: '2026-11-26T18:30', end: '2026-11-26T20:00', time_zone: 'Europe/Berlin' },
      theirs: { title: 'Training fällt aus', all_day: false, start: '2026-11-26T18:30', end: '2026-11-26T20:00', time_zone: 'Europe/Berlin' },
    }
    server(eventDetail({ title: 'Training fällt aus', conflict: clash }), (method, path) => (method === 'POST' && path.endsWith('/conflict') ? ok(eventDetail({ title: 'Training (Halle 2)' })) : undefined))
    await open()
    await openEvent()
    const box2 = field('conflict')
    expect(box2.textContent).toContain('Anna hat diesen Termin eben auch geändert. Ihre Fassung gilt jetzt, deine stand kurz davor.')
    expect(box2.querySelector('.line-through')?.textContent).toContain('Training (Halle 2)')
    expect(box2.textContent).toContain('Fassung von Anna')
    expect(byText('So lassen', box2)).toBeDefined()
    await click(field('conflict-restore'))
    expect(calls.find((call) => call.path === '/api/events/7/conflict')?.body).toEqual({ restore: true })
    expect(field<HTMLInputElement>('field-title').value).toBe('Training (Halle 2)')
  })

  it('TIME-5 AC-11 a shared event says who entered it and who changed it last', async () => {
    server(eventDetail({ shared: true, created_by: { id: 3, name: 'Ben', color: 2, avatar: null }, updated_by: { id: 2, name: 'Anna', color: 1, avatar: null }, updated_at: '2026-11-26T15:04:00Z' }))
    await open()
    await openEvent()
    expect(field('entered-by').textContent).toBe('Beingetragen von Ben')
    expect(field('panel-head').textContent).toBe('Persönlich · zuletzt geändert von Anna, vor 1 Minute')
  })

  it('TIME-5 EC-8 a second click on "Sichern" sends once, a retry after a broken connection is the same request', async () => {
    server()
    await open()
    await newEvent()
    const realFetch = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST' && String(input) === '/api/events') {
        calls.push({ method: 'POST', path: '/api/events', body: JSON.parse(String(init.body)), headers: {} })
        throw new TypeError('Failed to fetch')
      }
      return realFetch(input, init)
    }))
    type(field<HTMLInputElement>('field-title'), 'Zahnarzt')
    const save = field<HTMLButtonElement>('panel-save')
    await act(async () => {
      save.click()
      save.click()
    })
    expect(calls.filter((call) => call.method === 'POST' && call.path === '/api/events')).toHaveLength(1)
    expect(field('panel-message').textContent).toBe('Keine Verbindung zu nextime. Deine Eingaben bleiben stehen; sichere gleich noch einmal.')
    expect(field<HTMLInputElement>('field-title').value).toBe('Zahnarzt')
    await click(save)
    const refs = calls.filter((call) => call.method === 'POST' && call.path === '/api/events').map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs).toHaveLength(2)
    expect(refs[0]).toBe(refs[1])
  })

  it('TIME-5 EC-9 an event deleted meanwhile says by whom and keeps the inputs', async () => {
    server(eventDetail(), (method, path) => (method === 'PATCH' && path === '/api/events/7' ? { status: 409, body: { detail: { code: 'event_deleted', message: 'x', by: 'Anna' } } } : undefined))
    await open()
    await openEvent()
    type(field<HTMLInputElement>('field-title'), 'Training neu')
    await click(field('panel-save'))
    expect(field('panel-message').textContent).toBe('Anna hat diesen Termin eben gelöscht. Deine Eingaben bleiben zum Abschreiben stehen.')
    expect(field<HTMLInputElement>('field-title').value).toBe('Training neu')
    expect(calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({ title: 'Training neu', scope: 'all', etag: '"e1"' })
  })

  it('TIME-5 EC-9 a right to edit taken meanwhile says by whom and keeps the inputs', async () => {
    server(eventDetail(), (method, path) => (method === 'PATCH' && path === '/api/events/7' ? { status: 403, body: { detail: { code: 'calendar_read_only', message: 'x', owner: 'Anna' } } } : undefined))
    await open()
    await openEvent()
    type(field<HTMLInputElement>('field-title'), 'Training später')
    await click(field('panel-save'))
    expect(field('panel-message').textContent).toBe('Anna hat dir das Recht zum Bearbeiten entzogen. Deine Eingaben bleiben zum Abschreiben stehen.')
    expect(field<HTMLInputElement>('field-title').value).toBe('Training später')
  })
})

describe('TIME-5 dialog', () => {
  afterEach(() => vi.unstubAllGlobals())
  const narrow = (on: boolean) =>
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: on && query.includes('1023'), addEventListener: () => undefined, removeEventListener: () => undefined }))

  it('TIME-5 dialog from 1024 px the event opens in the middle as a dialog, with the calendar in its head', async () => {
    narrow(false)
    server(eventDetail({ calendar_id: 2 }))
    await open()
    await openEvent()
    expect(panel()!.getAttribute('data-shape')).toBe('dialog')
    const shell = field('event-shell')
    expect(shell.getAttribute('role')).toBe('dialog')
    expect(shell.getAttribute('aria-modal')).toBe('true')
    expect(shell.className).toContain('max-w-[620px]')
    expect(shell.className).toContain('rounded-[16px]')
    expect(field('panel-head').textContent).toContain('Familie')
    // The view keys wait while it is open.
    const before = location.pathname
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'm' })))
    expect(location.pathname).toBe(before)
  })

  it('TIME-5 dialog the footer: "Löschen" on the left, "Abbrechen" and "Sichern" on the right, no line above', async () => {
    narrow(false)
    server(eventDetail({ calendar_id: 2 }))
    await open()
    await openEvent()
    const footer = field('event-footer')
    expect([...footer.querySelectorAll('button')].map((button) => button.getAttribute('data-testid'))).toEqual(['panel-delete', 'panel-cancel', 'panel-save'])
    expect(footer.className).not.toContain('border-t')
    expect(field('panel-delete').className).toContain('mr-auto')
  })

  it('TIME-5 dialog Escape with changes asks first; Tab stays inside', async () => {
    narrow(false)
    server()
    await open()
    await newEvent()
    type(field<HTMLInputElement>('field-title'), 'Halb fertig')
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(document.querySelector('[data-testid="discard-question"]')).not.toBeNull()
    await click(byText('Weiter bearbeiten'))
    expect(panel()).not.toBeNull()
    // From the last control Tab goes to the first, Shift+Tab back.
    const shell = field('event-shell')
    const save = field<HTMLButtonElement>('panel-save')
    save.focus()
    await act(async () => void shell.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true })))
    expect(document.activeElement).toBe(field('panel-close'))
    await act(async () => void shell.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true })))
    expect(document.activeElement).toBe(save)
  })

  it('TIME-5 dialog below 1024 px it stays the panel at the edge', async () => {
    narrow(true)
    server(eventDetail({ calendar_id: 2 }))
    await open()
    await openEvent()
    expect(panel()!.getAttribute('data-shape')).toBe('panel')
    expect(field('event-shell').getAttribute('role')).toBeNull()
    expect(field('event-footer').className).toContain('border-t')
  })
})
