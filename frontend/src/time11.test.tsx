/** TIME-11 the overview "Heute": the start view, what comes next or runs, the rest of today, the past folded, the next
 * events from tomorrow on as the list of the column (asked for while testing, 2026-11-23), background in the head, the quick field, the jump to a calendar week, the clock that moves
 * on, loading, empty, many and long events, code as text, the device's clock, a phone. */
import { act } from 'react'

import App from './App'
import { announce } from './features/calendars/store'
import i18n from './i18n'
import { forgetPrefs, setPref } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, me, render, settle, start, stop, type } from './testing'
import { anna, at, BERLIN, NOW, theWeek, views } from './viewFakes'

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

const all = (id: string) => [...box.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const one = (id: string) => box.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const text = (id: string) => one(id)?.textContent ?? ''

describe('TIME-11 the overview', () => {
  it('TIME-11 AC-1 AC-7 opens as the start with "Heute", weekday, date and the calendar week', async () => {
    views({ rows: theWeek() })
    await open()
    expect(one('view-today')).not.toBeNull()
    expect(box.querySelector('h1')!.textContent).toBe('Heute')
    expect(text('today-date')).toBe('Donnerstag, 26. November · KW 48')
  })

  it('TIME-11 AC-7 without week numbers the heading has no week', async () => {
    views({ rows: [], more: (_m, path) => (path === '/api/auth/me' ? { status: 200, body: me({ display: { ...BERLIN, week_numbers: false } }) } : undefined) })
    await open()
    expect(text('today-date')).toBe('Donnerstag, 26. November')
    expect(one('week-number')).toBeNull()
  })

  it('TIME-11 AC-2 the next event comes large: "in 25 Minuten", time, place, calendar, and the person with her name', async () => {
    views({ rows: theWeek() })
    await open()
    expect(text('next-label')).toBe('Als Nächstes · in 25 Minuten')
    const next = one('next')!
    expect(next.textContent).toContain('Zahnarzt Ben')
    expect(next.textContent).toContain('16:30–17:15 · Praxis Dr. Weber, Lindenstraße 4 · Familie')
    expect(text('next-person')).toBe('AAnna')
    const rest = all('today-row').map((row) => row.textContent)
    expect(rest).toEqual(['20:30–21:00Telefonat mit Oma Familie'])
  })

  it('TIME-11 AC-3 AC-4 next events: what comes from tomorrow on as the list of the column, background only in the head of today', async () => {
    views({ rows: theWeek() })
    await open()
    expect(all('column-title').map((title) => title.textContent)).toEqual(['Werkstatt', 'Fußball Ben', 'Wochenmarkt-Stand', 'Kaffee bei Oma', 'Belegt'])
    expect(all('column-sub')[0].textContent).toBe('morgen · 08:15')
    // "Demnächst" for the week, then the month by name.
    expect(all('column-month').map((node) => node.textContent)).toEqual(['Demnächst', 'Dezember 2026'])
    // Holidays and the like are no next events; today's background stands beside the heading.
    expect(one('next-events')!.textContent).not.toContain('1. Advent')
    expect(text('background-today')).toBe('Gelbe Tonne')
    expect(one('today-column')!.textContent).not.toContain('Gelbe Tonne')
    const asked = new URL(calls.find((call) => call.path.startsWith('/api/search/upcoming'))!.path, 'http://x').searchParams
    expect(asked.get('from')).toBe('2026-11-27')
    expect(asked.get('background')).toBe('false')
    expect(asked.get('limit')).toBe('10')
  })

  it('TIME-11 AC-8 EC-8 the past events fold into one quiet line, "Belegt (Anna)" without title; a click opens and closes', async () => {
    views({ rows: theWeek() })
    await open()
    const past = one('past')!
    expect(past.textContent).toBe('Vorbei: Teamrunde, Mittag mit Chris, Belegt (Anna)3 ▾')
    expect(past.getAttribute('aria-expanded')).toBe('false')
    expect(all('today-row')).toHaveLength(1)
    await click(past)
    expect(one('past')!.getAttribute('aria-expanded')).toBe('true')
    expect(all('today-row').map((row) => row.textContent)).toContain('14:00–16:00Belegt (Anna) Anna · Arbeit')
    await click(one('past'))
    expect(all('today-row')).toHaveLength(1)
  })

  it('TIME-11 next events the head "Nächste Termine", the sheet in the color of each calendar, "morgen" in the accent, nothing of today', async () => {
    views({ rows: theWeek() })
    await open()
    const heading = one('next-events')!.querySelector('h2')!
    expect(heading.textContent).toBe('Nächste Termine')
    expect(heading.className).toContain('uppercase')
    const sheets = all('column-date')
    expect(sheets[0].textContent).toBe('Fr27')
    const head = (index: number) => (sheets[index].children[2] as HTMLElement).style.color
    expect(head(0)).toBe('var(--color-cal-orange)')
    expect(head(1)).toBe('var(--color-cal-slate)')
    expect(head(2)).toBe('var(--color-cal-green)')
    const distance = all('column-distance')[0]
    expect(distance.textContent).toBe('morgen')
    expect(distance.className).toContain('text-accent-400')
    // What is still to come today stands on the left only; no day stands as "frei".
    expect(one('next-events')!.textContent).not.toContain('Telefonat mit Oma')
    expect(one('next-events')!.textContent).not.toContain('frei')
    expect(one('calendar-column')).toBeNull()
  })

  it('TIME-11 next events nothing more to come: "Keine kommenden Termine." as a calm empty state, no days', async () => {
    views({ rows: [at(1, 'Heute noch', '2026-11-26 20:00', '2026-11-26 21:00'), at(5, 'Nikolaus', '2026-12-06', '2026-12-06')] })
    await open()
    expect(one('next-events-empty')!.className).toContain('nt-empty')
    expect(text('next-events-empty')).toBe('Keine kommenden Termine.')
    expect(all('column-row')).toHaveLength(0)
    expect(one('next-events-more')).toBeNull()
  })

  it('TIME-11 AC-11 next events ten at first, "Weitere anzeigen" brings the next ten', async () => {
    views({ rows: Array.from({ length: 12 }, (_, index) => at(1, `Termin ${index + 1}`, `2026-12-${String(index + 1).padStart(2, '0')} 10:00`, `2026-12-${String(index + 1).padStart(2, '0')} 11:00`)) })
    await open()
    expect(all('column-row')).toHaveLength(10)
    await click(one('next-events-more'))
    await settle()
    expect(all('column-row')).toHaveLength(12)
    expect(all('column-title').at(-1)!.textContent).toBe('Termin 12')
    expect(one('next-events-more')).toBeNull()
    const asked = calls.filter((call) => call.path.startsWith('/api/search/upcoming')).map((call) => new URL(call.path, 'http://x').searchParams.get('offset'))
    expect(asked).toEqual(['0', '10'])
  })

  it('TIME-11 AC-12 the field "Schnell eintragen" stands on top', async () => {
    views({ rows: theWeek() })
    await open()
    const field = one('quick-entry') as HTMLInputElement
    expect(field.getAttribute('aria-label')).toBe('Schnell eintragen')
    expect(field.compareDocumentPosition(box.querySelector('h1')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    type(field, 'Zahnarzt')
    await act(async () => field.form!.requestSubmit())
    await settle()
    expect((document.querySelector('[data-testid="field-title"]') as HTMLInputElement).value).toBe('Zahnarzt')
  })

  it('TIME-11 AC-13 the key K and a click on the week open the jump; KW 2 2027 shows 11 to 17 January in the week', async () => {
    views({ rows: theWeek() })
    await open()
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k' })))
    expect(one('week-jump')).not.toBeNull()
    await click(document.querySelector('[data-testid="week-jump"] button[type="button"]'))
    expect(one('week-jump')).toBeNull()
    await click(one('week-button'))
    type(one('jump-week') as HTMLInputElement, '2')
    type(one('jump-year') as HTMLInputElement, '2027')
    await act(async () => (one('week-jump') as HTMLFormElement).requestSubmit())
    await settle()
    expect(one('view-week')).not.toBeNull()
    expect(text('period')).toBe('11.–17. Januar 2027')
    expect(text('week-number')).toBe('KW 2')
  })
})

describe('TIME-11 the edges', () => {
  it('TIME-11 EC-1 everything past at 22:40: "Heute nichts mehr", the next tomorrow 08:15, "Vorbei: 5 Termine ▾"', async () => {
    vi.setSystemTime(new Date('2026-11-26T21:40:00Z'))
    views({ rows: theWeek() })
    await open()
    expect(one('next')).toBeNull()
    expect(text('nothing-more-title')).toBe('Heute nichts mehr')
    expect(one('nothing-more')!.textContent).toContain('Als Nächstes: morgen 08:15, Werkstatt')
    expect(text('past')).toBe('Vorbei: 5 Termine ▾')
  })

  it('TIME-11 EC-2 a holiday of a week stands once at the top as "Tag 3 von 7", not on every day', async () => {
    views({ rows: [...theWeek(), at(2, 'Urlaub', '2026-11-24', '2026-11-30')] })
    await open()
    const rows = all('today-row').map((row) => row.textContent)
    expect(rows[0]).toBe('Tag 3 von 7Urlaub Persönlich')
    expect(one('next')!.textContent).not.toContain('Urlaub')
    expect(one('next-events')!.textContent).not.toContain('Urlaub')
  })

  it('TIME-11 EC-3 the count moves on, turns to "läuft noch", and the next day comes after midnight', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
    vi.setSystemTime(NOW)
    views({ rows: theWeek() })
    await open()
    expect(text('next-label')).toBe('Als Nächstes · in 25 Minuten')
    await act(async () => void vi.advanceTimersByTime(10 * 60_000))
    expect(text('next-label')).toBe('Als Nächstes · in 15 Minuten')
    await act(async () => void vi.advanceTimersByTime(20 * 60_000))
    expect(text('next-label')).toBe('Läuft noch 40 Minuten')
    vi.setSystemTime(new Date('2026-11-26T22:59:00Z'))
    await act(async () => void vi.advanceTimersByTime(30_000))
    expect(text('today-date')).toContain('Donnerstag, 26. November')
    await act(async () => void vi.advanceTimersByTime(2 * 60_000))
    await settle()
    expect(text('today-date')).toContain('Freitag, 27. November')
    const asked = calls.filter((call) => call.path.startsWith('/api/events?')).map((call) => new URL(call.path, 'http://x').searchParams.get('start'))
    expect(asked.at(-1)).toBe('2026-11-26T23:00:00.000Z')
    expect(text('next-label')).toBe('Als Nächstes · um 08:15')
  })

  it('TIME-11 EC-4 while the events come, gray bars stand and the heading with the date is there', async () => {
    views({ rows: theWeek() })
    const fake = globalThis.fetch
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => (String(input).startsWith('/api/events?') ? new Promise(() => undefined) : fake(input, init)))
    await open()
    expect(one('today-loading')).not.toBeNull()
    expect(text('today-date')).toBe('Donnerstag, 26. November · KW 48')
  })

  it('TIME-11 EC-9 back from the week, Today stands at once with what it showed, no gray bars in between', async () => {
    views({ rows: theWeek() })
    await open()
    expect(one('today-loading')).toBeNull()
    const link = (label: string) => [...box.querySelectorAll('nav a')].find((entry) => entry.textContent === label)
    await click(link('Woche'))
    await settle()
    const fake = globalThis.fetch
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => (String(input).startsWith('/api/events?') ? new Promise(() => undefined) : fake(input, init)))
    await click(link('Heute'))
    expect(one('view-today')).not.toBeNull()
    expect(one('today-loading')).toBeNull()
  })

  it('TIME-11 EC-6 a new account: a calm empty state, the field usable, no next events', async () => {
    views({ rows: [] })
    await open()
    expect(text('nothing-more-title')).toBe('Heute steht nichts an.')
    expect(text('next-events-empty')).toBe('Keine kommenden Termine.')
    expect(all('column-row')).toHaveLength(0)
    expect((one('quick-entry') as HTMLInputElement).disabled).toBe(false)
  })

  it('TIME-11 EC-6 an empty collection says so', async () => {
    setPref('collection', 1, '9')
    views({ rows: theWeek(), collections: [{ id: 9, name: 'Garten', calendar_ids: [], updated_at: '' }] })
    await open()
    expect(text('empty-collection')).toContain('In der Sammlung „Garten“ ist gerade kein Kalender zu sehen.')
    expect(calls.some((call) => call.path.startsWith('/api/events?'))).toBe(false)
  })

  it('TIME-11 EC-7 thirty events with long titles all stand and wrap instead of being cut off', async () => {
    const long = 'Sehr langer Titel '.repeat(12).slice(0, 200)
    const rows = Array.from({ length: 30 }, (_, index) => at(1, `${index} ${long}`, `2026-11-26 ${String(17 + Math.floor(index / 6)).padStart(2, '0')}:${String((index % 6) * 10).padStart(2, '0')}`, '2026-11-26 23:00'))
    views({ rows })
    await open()
    expect(all('next').length + all('today-row').length).toBe(30)
    for (const row of all('today-row')) {
      const title = row.querySelector('.nt-wrap')!
      expect(title.className).not.toContain('truncate')
      expect(title.textContent).toContain(long)
    }
  })

  it('TIME-11 EC-9 a change made elsewhere shows by itself', async () => {
    const rows = theWeek()
    views({ rows })
    await open()
    rows.push(at(1, 'Elternsprechtag', '2026-11-26 19:00', '2026-11-26 19:30'))
    await act(async () => announce())
    await settle()
    expect(all('today-row').map((row) => row.textContent)).toContain('19:00–19:30Elternsprechtag Familie')
  })

  it('TIME-11 EC-10 a title with code stays text', async () => {
    views({ rows: [at(1, '<img src=x onerror="window.hacked=1">', '2026-11-26 18:00', '2026-11-26 19:00')] })
    await open()
    expect(box.querySelector('img[src="x"]')).toBeNull()
    expect(one('next')!.textContent).toContain('<img src=x onerror="window.hacked=1">')
  })

  it('TIME-11 EC-11 today, past and "in 25 Minuten" come from the device clock: the window starts at its midnight', async () => {
    views({ rows: theWeek() })
    await open()
    const asked = new URL(calls.find((call) => call.path.startsWith('/api/events?'))!.path, 'http://x').searchParams
    expect(asked.get('start')).toBe('2026-11-25T23:00:00.000Z')
    vi.setSystemTime(new Date('2026-11-27T07:00:00Z'))
    await open()
    expect(text('today-date')).toBe('Freitag, 27. November · KW 48')
    expect(text('next-label')).toBe('Als Nächstes · in 15 Minuten')
  })

  it('TIME-11 EC-12 on a phone the collection stands in the head, then the field, today and the next events in one column', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }))
    views({ rows: theWeek() })
    await open()
    expect(text('collection-button-compact')).toBe('Alles ▾')
    const order = ['quick-entry', 'today-column', 'next-events'].map((id) => one(id)!)
    expect(order[0].compareDocumentPosition(order[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(order[1].compareDocumentPosition(order[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('TIME-11 EC-13 the running one large, the next first of the rest; two starting together both in the block', async () => {
    vi.setSystemTime(new Date('2026-11-26T15:40:00Z'))
    const rows = [at(1, 'Zahnarzt Ben', '2026-11-26 16:30', '2026-11-26 17:15', { person: anna }), at(7, 'Training', '2026-11-26 18:30', '2026-11-26 20:00')]
    views({ rows })
    await open()
    expect(text('next-label')).toBe('Läuft noch 35 Minuten')
    expect(one('next')!.textContent).toContain('Zahnarzt Ben')
    expect(all('today-row')[0].textContent).toContain('Training')
    vi.setSystemTime(new Date('2026-11-26T16:20:00Z'))
    views({ rows: [...rows, at(2, 'Elternabend', '2026-11-26 18:30', '2026-11-26 21:00')] })
    await open()
    expect(all('next').map((block) => block.querySelector('strong')!.textContent)).toEqual(['Training', 'Elternabend'])
    expect(text('next-label')).toBe('Als Nächstes · um 18:30')
  })

  it('TIME-11 EC-14 an all-day event of today stands as "ganztägig" above the timed ones, never as next', async () => {
    views({ rows: [at(1, 'Ausflug', '2026-11-26', '2026-11-26'), at(1, 'Telefonat mit Oma', '2026-11-26 20:30', '2026-11-26 21:00')] })
    await open()
    expect(one('next')!.textContent).toContain('Telefonat mit Oma')
    expect(all('today-row')[0].textContent).toBe('ganztägigAusflug Familie')
  })

  it('TIME-11 AC-6 only the calendars of the chosen collection', async () => {
    setPref('collection', 1, '8')
    views({ rows: theWeek(), collections: [{ id: 8, name: 'Familie', calendar_ids: [1, 6], updated_at: '' }] })
    await open()
    const asked = new URL(calls.find((call) => call.path.startsWith('/api/events?'))!.path, 'http://x').searchParams
    expect(asked.get('calendars')).toBe('1,6')
    expect(one('today-column')!.textContent).not.toContain('Teamrunde')
    expect(text('background-today')).toBe('Gelbe Tonne')
    // The next events too: only the collection's calendars.
    const next = new URL(calls.find((call) => call.path.startsWith('/api/search/upcoming'))!.path, 'http://x').searchParams
    expect(next.get('calendars')).toBe('1,6')
    expect(all('column-title').map((title) => title.textContent)).toEqual(['Fußball Ben', 'Kaffee bei Oma'])
  })

  it('TIME-11 AC-2 EC-2 an evening until after midnight is no long event: next at 21:30, not "Tag 1 von 2"', async () => {
    views({ rows: [at(1, 'Kino', '2026-11-26 21:30', '2026-11-27 00:15')] })
    await open()
    expect(text('next-label')).toBe('Als Nächstes · um 21:30')
    expect(one('next')!.textContent).toContain('21:30–00:15')
    expect(all('today-row')).toHaveLength(0)
    expect(one('nothing-more')).toBeNull()
  })

  it('TIME-11 EC-1 EC-2 a night from yesterday 22:00 to 01:00 runs until 01:00 and is past after', async () => {
    const night = at(1, 'Nachtschicht', '2026-11-25 22:00', '2026-11-26 01:00')
    vi.setSystemTime(new Date('2026-11-25T23:30:00Z'))
    views({ rows: [night] })
    await open()
    expect(text('next-label')).toBe('Läuft noch 30 Minuten')
    vi.setSystemTime(NOW)
    await open()
    expect(one('next')).toBeNull()
    expect(text('past')).toBe('Vorbei: 1 Termin ▾')
    expect(one('today-column')!.textContent).not.toContain('Tag 2 von 2')
  })

  it('TIME-11 EC-2 times over 24 hours and more stand once as "Tag n von m"', async () => {
    views({ rows: [at(2, 'Messe', '2026-11-25 09:00', '2026-11-27 18:00'), at(1, 'Telefonat mit Oma', '2026-11-26 20:30', '2026-11-26 21:00')] })
    await open()
    expect(all('today-row')[0].textContent).toBe('Tag 2 von 3Messe Persönlich')
    expect(one('next')!.textContent).toContain('Telefonat mit Oma')
    expect(one('next-events')!.textContent).not.toContain('Messe')
  })
})
