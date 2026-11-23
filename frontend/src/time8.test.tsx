/** TIME-8 in the interface: the place field with suggestions while typing (address with the place's name below, „…“
 * als Text übernehmen, the sentence about the free service), only the place going out, answers out of order, the
 * operator's switch, the small map with "© OpenStreetMap" and "In Karten öffnen" per device, a web address as a link,
 * free text and markup as text, a map that does not come, "busy only". */
import { act } from 'react'

import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { EventDetail } from './features/calendars/api'
import { mapsLink, placesApi, type Suggestion } from './features/places/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, calls, click, me, ok, render, serve, settle, signedIn, start, stop } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })

const SUGGESTIONS: Suggestion[] = [
  { address: 'Lindenstraße 4, 10115 Berlin', name: 'Praxis Dr. Weber', value: 'Praxis Dr. Weber, Lindenstraße 4, 10115 Berlin', lat: 52.517, lon: 13.3889 },
  { address: 'Lindenstraße 40, 10115 Berlin', name: '', value: 'Lindenstraße 40, 10115 Berlin', lat: 52.52, lon: 13.4 },
]

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-26T15:05:00Z'))
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function server(detail: EventDetail | null, places = true) {
  serve(
    signedIn(anna, (method, path) => {
      if (path.startsWith('/api/calendars')) return ok(calendarList([calendar(1, 'Persönlich', { default: true })], { places }))
      if (path.startsWith('/api/places/lookup')) return ok({ place: { lat: 52.5, lon: 13.4 }, unavailable: false })
      if (path.startsWith('/api/places?')) return ok({ suggestions: SUGGESTIONS, unavailable: false })
      if (path.startsWith('/api/events?') && detail)
        return ok([{ id: detail.id, occurrence: null, calendar_id: 1, title: detail.title, busy: true, busy_only: detail.busy_only, recurring: false, all_day: false, start: '2026-11-26T17:30:00Z', end: '2026-11-26T19:00:00Z', time_zone: 'Europe/Berlin' }])
      if (detail && path.startsWith(`/api/events/${detail.id}`) && method === 'GET') return ok(detail)
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

const field = <T extends HTMLElement>(id: string) => document.querySelector<T>(`[data-testid="${id}"]`)
const pause = (ms: number) => act(async () => await new Promise((resolve) => setTimeout(resolve, ms)))

function typeInto(input: HTMLInputElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value)
  act(() => void input.dispatchEvent(new Event('input', { bubbles: true })))
}

async function newEvent(title = 'Zahnarzt'): Promise<void> {
  await click(box.querySelector('[data-testid="new-event"]'))
  typeInto(field<HTMLInputElement>('field-title')!, title)
}

async function openEvent(): Promise<void> {
  await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
}

describe('TIME-8 the place field', () => {
  it('TIME-8 AC-1 AC-5 suggestions bring the address with the name below, "als Text übernehmen" last, and the sentence; only the place goes out', async () => {
    server(null)
    await open()
    await newEvent('Geheimer Termin')
    typeInto(field<HTMLInputElement>('field-location')!, 'Lindenstr')
    await pause(350)
    await settle()
    const options = [...document.querySelectorAll('[role="option"]')].map((option) => option.textContent)
    expect(options).toEqual(['Lindenstraße 4, 10115 BerlinPraxis Dr. Weber', 'Lindenstraße 40, 10115 Berlin', '„Lindenstr“ als Text übernehmen'])
    expect(field('place-hint')!.textContent).toBe('Vorschläge kommen von einem freien Dienst; dabei geht nur der Ort hinaus, nie der Titel.')
    const asked = calls.filter((call) => call.path.startsWith('/api/places?'))
    expect(asked.map((call) => call.path)).toEqual(['/api/places?q=Lindenstr'])
    expect(asked[0].path).not.toContain('Geheim')
  })

  it('TIME-8 AC-1 a chosen suggestion saves the place with its point; "als Text übernehmen" the text alone', async () => {
    server(null)
    await open()
    await newEvent()
    typeInto(field<HTMLInputElement>('field-location')!, 'Lindenstr')
    await pause(350)
    await settle()
    await click(field('place-suggestion'))
    expect(field<HTMLInputElement>('field-location')!.value).toBe('Praxis Dr. Weber, Lindenstraße 4, 10115 Berlin')
    expect(field<HTMLImageElement>('place-map')!.getAttribute('src')).toBe('/api/places/map?lat=52.517&lon=13.3889')
    expect(field('place-copyright')!.textContent).toBe('© OpenStreetMap')
    await click(field('panel-save'))
    const made = calls.find((call) => call.method === 'POST' && call.path === '/api/events')!.body as Record<string, unknown>
    expect(made.location).toBe('Praxis Dr. Weber, Lindenstraße 4, 10115 Berlin')
    expect(made.geo).toEqual({ lat: 52.517, lon: 13.3889 })
  })

  it('TIME-8 EC-5 when nothing fits, "als Text übernehmen" stays and takes the text without a point', async () => {
    server(null)
    vi.spyOn(placesApi, 'suggest').mockResolvedValue({ suggestions: [], unavailable: false })
    await open()
    await newEvent()
    typeInto(field<HTMLInputElement>('field-location')!, 'bei Oma')
    await pause(350)
    await settle()
    expect([...document.querySelectorAll('[role="option"]')].map((option) => option.textContent)).toEqual(['„bei Oma“ als Text übernehmen'])
    await click(field('place-as-text'))
    expect(field('place-suggestions')).toBeNull()
    expect(field('place-map')).toBeNull()
    await click(field('panel-save'))
    const made = calls.find((call) => call.method === 'POST' && call.path === '/api/events')!.body as Record<string, unknown>
    expect(made.location).toBe('bei Oma')
    expect(made.geo).toBeNull()
  })

  it('TIME-8 EC-8 answers arriving out of order show the ones for what was typed last', async () => {
    server(null)
    const waiting: ((value: { suggestions: Suggestion[]; unavailable: boolean }) => void)[] = []
    vi.spyOn(placesApi, 'suggest').mockImplementation(() => new Promise((resolve) => waiting.push(resolve)))
    await open()
    await newEvent()
    const input = field<HTMLInputElement>('field-location')!
    typeInto(input, 'Lind')
    await pause(350)
    typeInto(input, 'Lindenstraße 40')
    await pause(350)
    expect(waiting).toHaveLength(2)
    await act(async () => waiting[1]({ suggestions: [SUGGESTIONS[1]], unavailable: false }))
    await act(async () => waiting[0]({ suggestions: [SUGGESTIONS[0]], unavailable: false }))
    await settle()
    expect([...document.querySelectorAll('[data-testid="place-suggestion"]')].map((option) => option.textContent)).toEqual(['Lindenstraße 40, 10115 Berlin'])
  })

  it('TIME-8 EC-3 a service that does not answer leaves only "als Text übernehmen", and saving goes on', async () => {
    server(null)
    vi.spyOn(placesApi, 'suggest').mockRejectedValue(new Error('unreachable'))
    await open()
    await newEvent()
    typeInto(field<HTMLInputElement>('field-location')!, 'Lindenstraße 4')
    await pause(350)
    await settle()
    expect([...document.querySelectorAll('[role="option"]')].map((option) => option.textContent)).toEqual(['„Lindenstraße 4“ als Text übernehmen'])
    await click(field('panel-save'))
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/events')).toBe(true)
  })

  it('TIME-8 AC-6 switched off: no suggestions, no sentence, no map; "In Karten öffnen" stays', async () => {
    server(eventDetail({ location: 'Lindenstraße 4, Berlin', place: { lat: 52.5, lon: 13.4 } }), false)
    await open()
    await openEvent()
    expect(field('place-map')).toBeNull()
    expect(field('place-open')!.textContent).toBe('In Karten öffnen')
    typeInto(field<HTMLInputElement>('field-location')!, 'Lindenstraße 40')
    await pause(350)
    await settle()
    expect(field('place-suggestions')).toBeNull()
    expect(field('place-hint')).toBeNull()
    expect(calls.some((call) => call.path.startsWith('/api/places'))).toBe(false)
  })

  it('TIME-8 EC-4 an empty place shows neither place, map nor "In Karten öffnen"', async () => {
    server(eventDetail({ location: '', writable: false, right: 'read' }))
    await open()
    await openEvent()
    expect(field('place-view')).toBeNull()
    expect(field('place-open')).toBeNull()
  })
})

describe('TIME-8 the place of an event', () => {
  it('TIME-8 AC-2 AC-3 a found address shows its map with © OpenStreetMap and opens the maps app', async () => {
    server(eventDetail({ location: 'Praxis Dr. Weber, Lindenstraße 4', place: { lat: 52.517, lon: 13.3889 }, writable: false, right: 'read' }))
    await open()
    await openEvent()
    expect(field<HTMLImageElement>('place-map')!.getAttribute('src')).toBe('/api/places/map?lat=52.517&lon=13.3889')
    expect(field<HTMLImageElement>('place-map')!.getAttribute('alt')).toBe('Karte mit Markierung: Praxis Dr. Weber, Lindenstraße 4')
    expect(field('place-copyright')!.textContent).toBe('© OpenStreetMap')
    const link = field<HTMLAnchorElement>('place-open')!
    expect(link.getAttribute('href')).toBe('https://www.google.com/maps/search/?api=1&query=52.517%2C13.3889')
    expect(field<HTMLAnchorElement>('place-text')!.getAttribute('href')).toBe(link.getAttribute('href'))
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('TIME-8 AC-3 Apple devices open Apple Maps, Android the system\'s app', () => {
    const agent = Object.getOwnPropertyDescriptor(window.navigator, 'userAgent')
    try {
      Object.defineProperty(window.navigator, 'userAgent', { configurable: true, value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X)' })
      expect(mapsLink('Lindenstraße 4', null)).toBe('https://maps.apple.com/?q=Lindenstra%C3%9Fe%204')
      expect(mapsLink('Lindenstraße 4', { lat: 52.5, lon: 13.4 })).toBe('https://maps.apple.com/?ll=52.5,13.4&q=Lindenstra%C3%9Fe%204')
      Object.defineProperty(window.navigator, 'userAgent', { configurable: true, value: 'Mozilla/5.0 (Linux; Android 15)' })
      expect(mapsLink('bei Oma', null)).toBe('geo:0,0?q=bei%20Oma')
    } finally {
      if (agent) Object.defineProperty(window.navigator, 'userAgent', agent)
      else delete (window.navigator as { userAgent?: string }).userAgent
    }
  })

  it('TIME-8 AC-4 EC-2 a place without a point is looked up only when it looks like an address; "bei Oma" searches in the maps app', async () => {
    server(eventDetail({ location: 'bei Oma', writable: false, right: 'read' }))
    await open()
    await openEvent()
    expect(field('place-map')).toBeNull()
    expect(calls.some((call) => call.path.startsWith('/api/places/lookup'))).toBe(false)
    expect(field<HTMLAnchorElement>('place-open')!.getAttribute('href')).toBe('https://www.google.com/maps/search/?api=1&query=bei%20Oma')
  })

  it('TIME-8 AC-4 a place from Google without a point but with an address gets its map', async () => {
    server(eventDetail({ location: 'Lindenstraße 4, Berlin', writable: false, right: 'read', source: 'google' }))
    await open()
    await openEvent()
    await settle()
    expect(calls.filter((call) => call.path.startsWith('/api/places/lookup')).map((call) => call.path)).toEqual(['/api/places/lookup?q=Lindenstra%C3%9Fe+4%2C+Berlin'])
    expect(field<HTMLImageElement>('place-map')!.getAttribute('src')).toBe('/api/places/map?lat=52.5&lon=13.4')
  })

  it('TIME-8 AC-7 a web address is a link, without map and without "In Karten öffnen"', async () => {
    server(eventDetail({ location: 'https://meet.example.com/abc', writable: false, right: 'read' }))
    await open()
    await openEvent()
    expect(field<HTMLAnchorElement>('place-link')!.getAttribute('href')).toBe('https://meet.example.com/abc')
    expect(field('place-open')).toBeNull()
    expect(field('place-map')).toBeNull()
  })

  it('TIME-8 EC-7 markup and javascript: stay text and go to the maps app only as a search', async () => {
    server(eventDetail({ location: '<img src=x onerror=alert(1)> javascript:alert(1)', writable: false, right: 'read' }))
    await open()
    await openEvent()
    expect(field('place-text')!.textContent).toBe('<img src=x onerror=alert(1)> javascript:alert(1)')
    // The only picture is nextime's own map, never one of the place's text.
    expect([...field('place-view')!.querySelectorAll('img')].map((image) => image.getAttribute('src')!.startsWith('/api/places/map?'))).not.toContain(false)
    for (const link of document.querySelectorAll<HTMLAnchorElement>('[data-testid="place-view"] a')) expect(link.getAttribute('href')!.startsWith('https://')).toBe(true)
  })

  it('TIME-8 EC-1 a map that does not come leaves the place and the way to the maps app', async () => {
    server(eventDetail({ location: 'Lindenstraße 4', place: { lat: 52.5, lon: 13.4 }, writable: false, right: 'read' }))
    await open()
    await openEvent()
    await act(async () => void field('place-map')!.dispatchEvent(new Event('error')))
    expect(field('place-map')).toBeNull()
    expect(field('place-text')!.textContent).toBe('Lindenstraße 4')
    expect(field('place-open')).not.toBeNull()
  })

  it('TIME-8 EC-6 a long place with line breaks stands whole', async () => {
    const place = 'Ärztehaus 🎉 Süd\nzweite Zeile ' + 'x'.repeat(300)
    server(eventDetail({ location: place, writable: false, right: 'read' }))
    await open()
    await openEvent()
    expect(field('place-text')!.textContent).toBe(place)
    expect(field('place-text')!.className).toContain('whitespace-pre-wrap')
  })

  it('TIME-8 EC-9 "busy only" shows no place and no map', async () => {
    server(eventDetail({ title: '', location: '', busy_only: true, writable: false, right: 'busy', place: null }))
    await open()
    await openEvent()
    expect(field('place-view')).toBeNull()
    expect(field('place-map')).toBeNull()
  })
})
