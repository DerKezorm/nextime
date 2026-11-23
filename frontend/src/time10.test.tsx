/** TIME-10 Quick entry: what a sentence means in German and English with the clock fixed on Thursday, 26 November
 * 2026, 16:05 and 23:59 in Berlin; the preview while typing in the account's clock; Enter saves once, "Mehr …" opens
 * the side panel; no date, an end before the start, a title too long, an empty field; pasted lines; a broken
 * connection; the calendar a collection suggests. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import { understand, type Context, type Understood } from './features/quick/understand'
import i18n from './i18n'
import { forgetPrefs, setPref } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, me, ok, render, settle, start, stop, type } from './testing'
import { BERLIN, CALENDARS, NOW, robin, views } from './viewFakes'

const LATE = new Date('2026-11-26T22:59:00Z').getTime()

const LIST = [
  { id: 1, name: 'Familie', writable: true },
  { id: 2, name: 'Persönlich', writable: true },
  { id: 3, name: 'Arbeit', writable: true },
  { id: 4, name: 'Anna Arbeit', writable: false },
  { id: 9, name: 'Verein', writable: true },
  { id: 7, name: 'Verein alt', writable: true },
  { id: 8, name: 'Familie', writable: true },
]

function at(now: number = NOW.getTime(), calendars = LIST): Context {
  return { now, zone: 'Europe/Berlin', calendars }
}

type Quick = Extract<Understood, { kind: 'event' }>

function event(text: string, context: Context = at()): Quick {
  const found = understand(text, context)
  if (found.kind !== 'event') throw new Error(`no event: ${text} → ${found.kind}`)
  return found
}

/** What matters of an understood event in one line: `date start–end title`. */
function line(found: Quick): string {
  return found.allDay ? `${found.date} ganztags ${found.title}` : `${found.date} ${found.start}–${found.endDate === found.date ? '' : found.endDate + ' '}${found.end} ${found.title}`
}

describe('TIME-10 understanding a sentence', () => {
  it.each([
    // AC-1, AC-3, AC-7: date and time in German and English, both ways of writing a time.
    ['morgen 15 uhr zahnarzt', '2026-11-27 15:00–16:00 Zahnarzt'],
    ['Morgen 15 Uhr Zahnarzt', '2026-11-27 15:00–16:00 Zahnarzt'],
    ['MORGEN 15 UHR ZAHNARZT', '2026-11-27 15:00–16:00 ZAHNARZT'],
    ['tomorrow 3 pm dentist', '2026-11-27 15:00–16:00 Dentist'],
    ['tomorrow 3pm dentist', '2026-11-27 15:00–16:00 Dentist'],
    ['morgen 15:30 Zahnarzt', '2026-11-27 15:30–16:30 Zahnarzt'],
    ['morgen um 15.30 Uhr Zahnarzt', '2026-11-27 15:30–16:30 Zahnarzt'],
    ['tomorrow at 3:30 pm call with Ben', '2026-11-27 15:30–16:30 Call with Ben'],
    ['tomorrow 12 am night train', '2026-11-27 00:00–01:00 Night train'],
    ['tomorrow 12 pm lunch', '2026-11-27 12:00–13:00 Lunch'],
    ['übermorgen 8 Uhr Flug', '2026-11-28 08:00–09:00 Flug'],
    ['day after tomorrow 8 am flight', '2026-11-28 08:00–09:00 Flight'],
    ['heute 18 Uhr Training', '2026-11-26 18:00–19:00 Training'],
    ['today 6 pm training', '2026-11-26 18:00–19:00 Training'],
    // AC-4: a length or an end.
    ['Fr 18 Uhr Kino mit Anna 2 Std #Familie', '2026-11-27 18:00–20:00 Kino mit Anna'],
    ['Fri 6 pm cinema 2 hours', '2026-11-27 18:00–20:00 Cinema'],
    ['morgen 10 Uhr Workshop 90 min', '2026-11-27 10:00–11:30 Workshop'],
    ['morgen 10 Uhr Workshop 1,5 Std', '2026-11-27 10:00–11:30 Workshop'],
    ['morgen 10 Uhr Workshop für eine halbe Stunde', '2026-11-27 10:00–10:30 Workshop'],
    ['tomorrow 10 am workshop for 2h', '2026-11-27 10:00–12:00 Workshop'],
    ['morgen 15 Uhr Zahnarzt bis 17 Uhr', '2026-11-27 15:00–17:00 Zahnarzt'],
    ['morgen 15–17 Uhr Zahnarzt', '2026-11-27 15:00–17:00 Zahnarzt'],
    ['morgen von 9 bis 11:30 Uhr Kurs', '2026-11-27 09:00–11:30 Kurs'],
    ['tomorrow 3-5 pm dentist', '2026-11-27 15:00–17:00 Dentist'],
    ['tomorrow 10 am until 1 pm workshop', '2026-11-27 10:00–13:00 Workshop'],
    // Dates.
    ['27.11. 10 Uhr Termin', '2026-11-27 10:00–11:00 Termin'],
    ['am 24.12.2026 Heiligabend', '2026-12-24 ganztags Heiligabend'],
    ['24.12.27 Heiligabend', '2027-12-24 ganztags Heiligabend'],
    ['2026-12-24 Heiligabend', '2026-12-24 ganztags Heiligabend'],
    ['24. Dezember 2027 Fest', '2027-12-24 ganztags Fest'],
    ['December 24 Christmas', '2026-12-24 ganztags Christmas'],
    ['on Dec 24th Christmas', '2026-12-24 ganztags Christmas'],
    ['the 3rd of March birthday', '2027-03-03 ganztags Birthday'],
    ['in 3 Tagen Abgabe', '2026-11-29 ganztags Abgabe'],
    ['in 2 weeks review 9 am', '2026-12-10 09:00–10:00 Review'],
    ['in einer Woche Abgabe', '2026-12-03 ganztags Abgabe'],
    ['nächsten Montag 9 Uhr Elternabend', '2026-11-30 09:00–10:00 Elternabend'],
    ['next monday 9 am parents evening', '2026-11-30 09:00–10:00 Parents evening'],
    ['am Freitag um 18 Uhr Kino', '2026-11-27 18:00–19:00 Kino'],
    ['Kino am Samstag 20 Uhr', '2026-11-28 20:00–21:00 Kino'],
    ['um 12 mittags Essen', '2026-11-27 12:00–13:00 Mittags Essen'],
    ['morgen mittags Essen', '2026-11-27 12:00–13:00 Essen'],
    // EC-2: what is left open.
    ['Donnerstag 18 Uhr Kino', '2026-11-26 18:00–19:00 Kino'],
    ['Donnerstag 15 Uhr Kino', '2026-12-03 15:00–16:00 Kino'],
    ['Thursday Cinema', '2026-12-03 ganztags Cinema'],
    ['15 Uhr Zahnarzt', '2026-11-27 15:00–16:00 Zahnarzt'],
    ['17:30 Zahnarzt', '2026-11-26 17:30–18:30 Zahnarzt'],
    ['um 15 Kaffee', '2026-11-27 15:00–16:00 Kaffee'],
    ['Montag Zahnarzt', '2026-11-30 ganztags Zahnarzt'],
    ['3. März Geburtstag Oma', '2027-03-03 ganztags Geburtstag Oma'],
    ['26. November Jahrestag', '2026-11-26 ganztags Jahrestag'],
    ['25. November Jahrestag', '2027-11-25 ganztags Jahrestag'],
    // EC-5: umlauts, emoji, blanks.
    ['  morgen 10 Uhr Kuchen 🎂 für Jürgen  ', '2026-11-27 10:00–11:00 Kuchen 🎂 für Jürgen'],
    ['morgen 10 Uhr äpfel pflücken', '2026-11-27 10:00–11:00 Äpfel pflücken'],
    // EC-6: lines pasted.
    ['morgen 9 Uhr\nZahnarzt\r\nmit Ben', '2026-11-27 09:00–10:00 Zahnarzt mit Ben'],
    // The place stays in the title (TIME-10 has none).
    ['morgen 19 Uhr Konzert in Köln', '2026-11-27 19:00–20:00 Konzert in Köln'],
    // "Do" alone is a word, not Thursday; before a time it is Thursday.
    ['Do the dishes tomorrow 8 pm', '2026-11-27 20:00–21:00 Do the dishes'],
    ['Do 18 Uhr Chor', '2026-11-26 18:00–19:00 Chor'],
  ])('TIME-10 AC-1 AC-3 AC-4 AC-7 EC-2 EC-5 "%s"', (text, expected) => {
    expect(line(event(text))).toBe(expected)
  })

  it('TIME-10 AC-4 without a length the event lasts an hour and says so; with one it does not', () => {
    expect(event('morgen 15 Uhr Zahnarzt').defaultLength).toBe(true)
    expect(event('morgen 15 Uhr Zahnarzt 2 Std').defaultLength).toBe(false)
    expect(event('morgen 15 Uhr Zahnarzt bis 16 Uhr').defaultLength).toBe(false)
  })

  it('TIME-10 EC-13 at 23:59 tomorrow is the device\'s tomorrow, today stays today', () => {
    const late = at(LATE)
    expect(line(event('morgen 9 Uhr Arzt', late))).toBe('2026-11-27 09:00–10:00 Arzt')
    expect(line(event('heute 23:30 Telefonat', late))).toBe('2026-11-26 23:30–2026-11-27 00:30 Telefonat')
    expect(line(event('Freitag Ausflug', late))).toBe('2026-11-27 ganztags Ausflug')
    expect(line(event('9 Uhr Arzt', late))).toBe('2026-11-27 09:00–10:00 Arzt')
    // In New York it is still 17:59 on Thursday.
    expect(line(event('19 Uhr Dinner', { ...late, zone: 'America/New_York' }))).toBe('2026-11-26 19:00–20:00 Dinner')
  })

  it('TIME-10 EC-13 an impossible date counts as no date, the night the clocks change as TIME-3 EC-1', () => {
    expect(understand('31. Februar Party', at())).toEqual({ kind: 'noDate', text: '31. Februar Party' })
    expect(understand('30.02. Party', at()).kind).toBe('noDate')
    expect(line(event('28. März 2027 2:30 Uhr Nachtzug'))).toBe('2027-03-28 03:30–04:30 Nachtzug')
    expect(line(event('29. Februar Schalttag'))).toBe('2028-02-29 ganztags Schalttag')
  })

  it('TIME-10 EC-1 no date at all', () => {
    expect(understand('Kino irgendwann', at())).toEqual({ kind: 'noDate', text: 'Kino irgendwann' })
    expect(understand('Zahnarzt Ben', at()).kind).toBe('noDate')
  })

  it('TIME-10 EC-3 empty or only blanks', () => {
    expect(understand('', at())).toEqual({ kind: 'empty' })
    expect(understand('   \n ', at())).toEqual({ kind: 'empty' })
  })

  it('TIME-10 AC-5 EC-5 EC-7 EC-8 the calendar after #, case alike, the longest name, the upper of two alike', () => {
    expect(event('#familie morgen 9 Uhr Elternabend').calendarId).toBe(1)
    expect(event('morgen 9 Uhr Elternabend #FAMILIE').title).toBe('Elternabend')
    expect(event('#Verein alt morgen 9 Uhr Treffen').calendarId).toBe(7)
    expect(event('#Verein alt morgen 9 Uhr Treffen').title).toBe('Treffen')
    expect(event('#Verein morgen 9 Uhr Treffen').calendarId).toBe(9)
    const twice = event('morgen 9 Uhr Treffen #Familie', at(NOW.getTime(), [LIST[6], LIST[0]]))
    expect(twice.calendarId).toBe(8)
    const unknown = event('#Garten morgen 9 Uhr Gießen')
    expect([unknown.calendarId, unknown.title, unknown.tag]).toEqual([null, '#Garten Gießen', { text: '#Garten', problem: 'unknown' }])
    const readOnly = event('morgen 9 Uhr Treffen #Anna Arbeit')
    expect([readOnly.calendarId, readOnly.tag?.problem, readOnly.title]).toEqual([null, 'readOnly', 'Treffen #Anna Arbeit'])
    expect(event('morgen 9 Uhr Treffen').calendarId).toBeNull()
  })

  it('TIME-10 AC-6 a repetition makes a series that starts on its first day', () => {
    const monday = event('jeden Montag 18 Uhr Training')
    expect([line(monday), monday.rule?.freq, monday.rule?.weekdays]).toEqual(['2026-11-30 18:00–19:00 Training', 'weekly', ['MO']])
    const two = event('every Monday and Thursday 6:30 pm Training')
    expect([line(two), two.rule?.weekdays]).toEqual(['2026-11-26 18:30–19:30 Training', ['MO', 'TH']])
    const daily = event('täglich 7 Uhr Joggen')
    expect([line(daily), daily.rule?.freq]).toEqual(['2026-11-27 07:00–08:00 Joggen', 'daily'])
    const weekdays = event('werktags 8 Uhr Arbeit')
    expect([line(weekdays), weekdays.rule?.weekdays]).toEqual(['2026-11-27 08:00–09:00 Arbeit', ['MO', 'TU', 'WE', 'TH', 'FR']])
    const every = event('alle 2 Wochen Montag 18 Uhr Chor')
    expect([line(every), every.rule?.interval, every.rule?.weekdays]).toEqual(['2026-11-30 18:00–19:00 Chor', 2, ['MO']])
    const monthly = event('monatlich 1. Dezember Miete')
    expect([line(monthly), monthly.rule?.freq]).toEqual(['2026-12-01 ganztags Miete', 'monthly'])
    const yearly = event('jedes Jahr 3. März Geburtstag Oma')
    expect([line(yearly), yearly.rule?.freq]).toEqual(['2027-03-03 ganztags Geburtstag Oma', 'yearly'])
    expect(event('montags 18 Uhr Training').rule?.weekdays).toEqual(['MO'])
    expect(event('wöchentlich morgen 9 Uhr Team').rule?.weekdays).toEqual(['FR'])
    expect(event('morgen 9 Uhr Team').rule).toBeNull()
  })

  it('TIME-10 EC-9 an end before the start is said, not saved', () => {
    expect(event('15 Uhr bis 14 Uhr Treffen').problem).toBe('end')
    expect(event('morgen 15:00-14:00 Treffen').problem).toBe('end')
  })

  it('TIME-10 EC-4 a title longer than 200 is never cut', () => {
    const long = event(`morgen 9 Uhr ${'a'.repeat(201)}`)
    expect([long.problem, long.title.length]).toEqual(['long', 201])
    expect(event(`morgen 9 Uhr ${'a'.repeat(200)}`).problem).toBeNull()
    expect(event('morgen 9 Uhr').problem).toBe('title')
  })
})

describe('TIME-10 the traps the review found', () => {
  it.each([
    // Finding 1: German "am" after a time is a preposition; English "am" is a.m.
    ['morgen um 18:30 am Bahnhof Treffen', '2026-11-27 18:30–19:30 Am Bahnhof Treffen'],
    ['12:00 am Bahnhof', '2026-11-27 12:00–13:00 Am Bahnhof'],
    ['9:30 am Rathaus', '2026-11-27 09:30–10:30 Am Rathaus'],
    ['Freitag 9:00 am Rathaus', '2026-11-27 09:00–10:00 Am Rathaus'],
    ['tomorrow 9:30 am meeting', '2026-11-27 09:30–10:30 Meeting'],
    ['tomorrow 9:30 am Meeting', '2026-11-27 09:30–10:30 Meeting'],
    ['tomorrow 9am Standup', '2026-11-27 09:00–10:00 Standup'],
    // Finding 3: weekday lists, spans of days, times of the day, prices, midnight.
    ['Mo, Mi 9 Uhr Kurs', '2026-11-30 09:00–10:00 Kurs'],
    ['heute abend 19 Uhr Essen', '2026-11-26 19:00–20:00 Essen'],
    ['heute Abend 7 Uhr Essen', '2026-11-26 19:00–20:00 Essen'],
    ['morgen nachmittag 3 Uhr Kaffee', '2026-11-27 15:00–16:00 Kaffee'],
    ['heute morgen 9 Uhr Joggen', '2026-11-26 09:00–10:00 Joggen'],
    ['morgen früh 7 Uhr Bäcker', '2026-11-27 07:00–08:00 Bäcker'],
    ['abends 8 Uhr Kino morgen', '2026-11-27 20:00–21:00 Kino'],
    ['morgen Pizza 9.50', '2026-11-27 ganztags Pizza 9.50'],
    ['morgen um 9.50 Bus', '2026-11-27 09:50–10:50 Bus'],
    ['morgen 9.50 Uhr Bus', '2026-11-27 09:50–10:50 Bus'],
    ['23 bis 1 Uhr Party', '2026-11-26 23:00–2026-11-27 01:00 Party'],
    ['heute 22:00-02:00 Nachtschicht', '2026-11-26 22:00–2026-11-27 02:00 Nachtschicht'],
    ['tomorrow 11-1 pm lunch', '2026-11-27 11:00–13:00 Lunch'],
    // Finding 4: spellings stay, "in" stays.
    ['morgen 9 Uhr iPhone reparieren', '2026-11-27 09:00–10:00 iPhone reparieren'],
    ['eBay Abholung morgen 10 Uhr', '2026-11-27 10:00–11:00 eBay Abholung'],
    ['morgen 15 Uhr in Köln essen', '2026-11-27 15:00–16:00 In Köln essen'],
    ['morgen 15 Uhr Essen in Köln', '2026-11-27 15:00–16:00 Essen in Köln'],
  ])('TIME-10 AC-7 EC-2 EC-5 "%s"', (text, expected) => {
    expect(line(event(text))).toBe(expected)
  })

  it('TIME-10 EC-1 a price or "9.11" without "um" or "Uhr" is no time', () => {
    expect(understand('Zahnarzt 9.11', at()).kind).toBe('noDate')
    expect(understand('Pizza 9.50', at()).kind).toBe('noDate')
  })

  it('TIME-10 EC-2 "nächsten Montag" on a Monday is the one in a week, "Montag" is today while its time still comes', () => {
    const monday = at(new Date('2026-11-30T09:00:00Z').getTime())
    expect(line(event('nächsten Montag 18 Uhr Chor', monday))).toBe('2026-12-07 18:00–19:00 Chor')
    expect(line(event('next Monday 6 pm choir', monday))).toBe('2026-12-07 18:00–19:00 Choir')
    expect(line(event('Montag 18 Uhr Chor', monday))).toBe('2026-11-30 18:00–19:00 Chor')
    expect(line(event('nächsten Montag Chor', monday))).toBe('2026-12-07 ganztags Chor')
    expect(line(event('nächsten Freitag Chor'))).toBe('2026-11-27 ganztags Chor')
  })

  it('TIME-10 AC-6 the nth and the last weekday of a month', () => {
    const second = event('jeden 2. Dienstag 19 Uhr Stammtisch')
    expect([line(second), second.rule?.freq, second.rule?.monthly]).toEqual(['2026-12-08 19:00–20:00 Stammtisch', 'monthly', 'nth'])
    const last = event('every last Friday 6 pm review')
    expect([line(last), last.rule?.monthly]).toEqual(['2026-11-27 18:00–19:00 Review', 'last'])
    expect(event('jeden 5. Freitag Treffen').rule?.monthly).toBe('last')
    expect(line(event('jeden 4. Donnerstag 18 Uhr Rat'))).toBe('2026-11-26 18:00–19:00 Rat')
    expect(line(event('jeden 4. Donnerstag 15 Uhr Rat'))).toBe('2026-12-24 15:00–16:00 Rat')
  })

  it('TIME-10 AC-6 "bis 31.12." after a repetition ends the series', () => {
    const training = event('Training jeden Montag 18 Uhr bis 31.12.')
    expect([line(training), training.rule?.end, training.rule?.until]).toEqual(['2026-11-30 18:00–19:00 Training', 'until', '2026-12-31'])
    const english = event('every Monday 6 pm training until December 31')
    expect([english.rule?.end, english.rule?.until, english.title]).toEqual(['until', '2026-12-31', 'Training'])
    expect(event('jeden Montag 18 Uhr Training bis 1.11.2026').problem).toBe('end')
    expect(understand('jeden Montag Training bis 31.2.', at()).kind).toBe('noDate')
  })

  it('TIME-10 AC-6 several weekdays without "jeden" are a weekly series on them', () => {
    const course = event('Mo, Mi 9 Uhr Kurs')
    expect([course.rule?.freq, course.rule?.weekdays]).toEqual(['weekly', ['MO', 'WE']])
    expect(event('jeden Di und Do 17 Uhr Schwimmen').rule?.weekdays).toEqual(['TU', 'TH'])
    expect(event('Sat and Sun 10 am brunch').rule?.weekdays).toEqual(['SA', 'SU'])
  })

  it('TIME-10 AC-4 a span of days is one event over them', () => {
    const holiday = event('von Montag bis Mittwoch Urlaub')
    expect([holiday.allDay, holiday.date, holiday.endDate, holiday.title]).toEqual([true, '2026-11-30', '2026-12-02', 'Urlaub'])
    const trip = event('27.11. bis 29.11. Ausflug')
    expect([trip.date, trip.endDate, trip.title]).toEqual(['2026-11-27', '2026-11-29', 'Ausflug'])
    const fair = event('vom 27.11. bis 29.11. 9 Uhr Messe')
    expect([fair.date, fair.start, fair.endDate, fair.end]).toEqual(['2026-11-27', '09:00', '2026-11-29', '10:00'])
    expect(event('29.11. bis 27.11. Ausflug').problem).toBe('end')
  })

  it('TIME-10 AC-4 a length without a time is said, never dropped', () => {
    const found = event('morgen 2 Std Zahnarzt')
    expect([found.problem, found.title, found.date]).toEqual(['noTime', 'Zahnarzt', '2026-11-27'])
  })

  it('TIME-10 EC-9 an end before the start stays a mistake unless it runs from the evening into the night', () => {
    expect(event('15 Uhr bis 14 Uhr Treffen').problem).toBe('end')
    expect(event('heute 22:00-02:00 Schicht').problem).toBeNull()
    expect(event('heute 12:00-02:00 Schicht').problem).toBe('end')
  })

  it('TIME-10 EC-7 a punctuation mark after #Name', () => {
    const found = event('#familie, morgen 9 Uhr Elternabend')
    expect([found.calendarId, found.title]).toEqual([1, 'Elternabend'])
    expect(event('morgen 9 Uhr Elternabend #Familie.').calendarId).toBe(1)
  })
})

// --- The field on "Heute" ----------------------------------------------------------------------------------------

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

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const text = (id: string) => one(id)?.textContent ?? ''

async function open(more?: (method: string, path: string, body: unknown) => { status: number; body: unknown } | undefined, account = robin): Promise<HTMLInputElement> {
  views({
    rows: [],
    more: (method, path, body) => more?.(method, path, body) ?? (path === '/api/auth/me' ? ok(account) : undefined),
  })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
  )
  return one('quick-entry') as HTMLInputElement
}

async function enter(field: HTMLInputElement): Promise<void> {
  await act(async () => field.form!.requestSubmit())
  await settle()
}

const posts = () => calls.filter((call) => call.method === 'POST' && call.path.startsWith('/api/events'))

function saved(body: unknown) {
  const sent = body as { title: string; calendar_id: number }
  return ok({ id: 77, title: sent.title, calendar_id: sent.calendar_id })
}

describe('TIME-10 the field "Schnell eintragen"', () => {
  it('TIME-10 AC-1 the preview while typing: title, day, time, length, calendar, "dein Standard"', async () => {
    const field = await open()
    type(field, 'morgen 15 uhr zahnarzt')
    expect(text('quick-title')).toBe('Zahnarzt')
    expect(text('quick-when')).toBe('Fr, 27. November · 15:00–16:00 (1 Stunde) · Familie, dein Standard')
    expect(one('quick-preview')!.textContent).toContain('Eingabe speichert')
    expect(field.getAttribute('placeholder')).toBe('Schnell eintragen, etwa „Morgen 15 Uhr Zahnarzt“')
  })

  it('TIME-10 AC-2 Enter saves and empties the field; "Mehr …" opens the event with all of it', async () => {
    const field = await open((method, path, body) => (method === 'POST' && path.startsWith('/api/events') ? saved(body) : undefined))
    type(field, 'Fr 18 Uhr Kino mit Anna 2 Std #Familie')
    await enter(field)
    expect(posts()).toHaveLength(1)
    expect(posts()[0].body).toMatchObject({ title: 'Kino mit Anna', all_day: false, start: '2026-11-27T18:00', end: '2026-11-27T20:00', time_zone: 'Europe/Berlin', calendar_id: 1 })
    expect(field.value).toBe('')
    expect(text('toast')).toBe('„Kino mit Anna“ eingetragen: Fr, 27. November, 18:00.')
    type(field, 'jeden Montag 18 Uhr Training #Arbeit')
    await act(async () => one('quick-more')!.click())
    await settle()
    expect((document.querySelector('[data-testid="field-title"]') as HTMLInputElement).value).toBe('Training')
    expect(field.value).toBe('')
    expect(posts()).toHaveLength(1)
  })

  it('TIME-10 AC-6 a series is saved with its rule and the preview says it as a sentence', async () => {
    const field = await open((method, path, body) => (method === 'POST' && path.startsWith('/api/events') ? saved(body) : undefined))
    type(field, 'jeden Montag 18 Uhr Training')
    expect(text('quick-when')).toBe('Jeden Montag, 18:00–19:00 · ab Mo, 30. November · Familie, dein Standard')
    await enter(field)
    expect(posts()[0].body).toMatchObject({ start: '2026-11-30T18:00', rule: { freq: 'weekly', interval: 1, weekdays: ['MO'], end: 'never' } })
  })

  it('TIME-10 AC-5 EC-7 the calendar: named with its color, otherwise the one the collection suggests', async () => {
    let field = await open()
    type(field, 'morgen 9 Uhr Team #Arbeit')
    expect(text('quick-when')).toBe('Fr, 27. November · 09:00–10:00 (1 Stunde) · Arbeit')
    expect(document.querySelector('[data-testid="quick-preview"] [data-color]')!.getAttribute('data-color')).toBe('teal')
    type(field, 'morgen 9 Uhr Team #Garten')
    expect(text('quick-tag')).toBe('„#Garten“ passt zu keinem Kalender, in den du eintragen darfst; der Termin geht in Familie.')
    expect(text('quick-title')).toBe('Team #Garten')
    // A collection without the default: its first writable calendar.
    setPref('collection', 1, '9')
    field = await open((_method, path) => (path.startsWith('/api/collections') ? ok({ collections: [{ id: 9, name: 'Job', calendar_ids: [3, 7], updated_at: '' }] }) : undefined))
    type(field, 'morgen 9 Uhr Team')
    expect(text('quick-when')).toBe('Fr, 27. November · 09:00–10:00 (1 Stunde) · Arbeit')
    // A collection with the default: the default.
    setPref('collection', 1, '10')
    field = await open((_method, path) => (path.startsWith('/api/collections') ? ok({ collections: [{ id: 10, name: 'Zuhause', calendar_ids: [2, 1], updated_at: '' }] }) : undefined))
    type(field, 'morgen 9 Uhr Team')
    expect(text('quick-when')).toContain('· Familie, dein Standard')
    // One calendar shown alone: that one; one only to be read: the default.
    forgetPrefs()
    setPref('only', 1, '7')
    field = await open()
    type(field, 'morgen 9 Uhr Team')
    expect(text('quick-when')).toContain('· Verein')
    setPref('only', 1, '4')
    field = await open()
    type(field, 'morgen 9 Uhr Team')
    expect(text('quick-when')).toContain('· Familie, dein Standard')
    // "Anna · Arbeit" may only be read: named, it stays in the title.
    type(field, 'morgen 9 Uhr Team #Anna · Arbeit')
    expect(one('quick-tag')!.textContent).toContain('ist nur lesbar')
  })

  it('TIME-10 EC-8 of two writable calendars alike the upper one, with its source', async () => {
    const list = calendarList([...CALENDARS.calendars, calendar(8, 'Familie', { group: 'icloud', color: 'blue' })])
    const field = await open((_method, path) => (path.startsWith('/api/calendars') ? ok(list) : undefined))
    type(field, 'morgen 9 Uhr Team #Familie')
    expect(text('quick-when')).toBe('Fr, 27. November · 09:00–10:00 (1 Stunde) · Familie (Eigene)')
  })

  it('TIME-10 AC-7 AC-3 twelve hours and English: as the member set the clock, whatever the language typed', async () => {
    const field = await open(undefined, me({ ...robin, display: { ...BERLIN, clock: '12' } }))
    type(field, 'morgen 15 Uhr Zahnarzt')
    expect(text('quick-when')).toBe('Fr, 27. November · 3:00 PM–4:00 PM (1 Stunde) · Familie, dein Standard')
    await act(async () => void (await i18n.changeLanguage('en')))
    await settle()
    type(field, 'morgen 15 Uhr Zahnarzt ')
    expect(text('quick-when')).toBe('Fri, November 27 · 3:00 PM–4:00 PM (1 hour) · Familie, your default')
  })

  it('TIME-10 EC-1 no date: the preview says so in orange, Enter opens the event with the text as title', async () => {
    const field = await open()
    type(field, 'Kino irgendwann')
    expect(text('quick-preview')).toBe('Kein Datum erkannt. Eingabe öffnet den Termin mit „Kino irgendwann“ als Titel.')
    expect(one('quick-preview')!.className).toContain('text-warn-500')
    await enter(field)
    expect(posts()).toHaveLength(0)
    expect((document.querySelector('[data-testid="field-title"]') as HTMLInputElement).value).toBe('Kino irgendwann')
  })

  it('TIME-10 EC-9 an end before the start: said, and Enter opens the event', async () => {
    const field = await open()
    type(field, 'morgen 15 Uhr bis 14 Uhr Treffen')
    expect(text('quick-preview')).toBe('Das Ende liegt vor dem Beginn. Eingabe öffnet den Termin.')
    await enter(field)
    expect(posts()).toHaveLength(0)
    expect((document.querySelector('[data-testid="field-title"]') as HTMLInputElement).value).toBe('Treffen')
  })

  it('TIME-10 EC-3 EC-4 an empty field does nothing; 201 characters are said and not saved', async () => {
    const field = await open()
    type(field, '   ')
    await enter(field)
    expect(posts()).toHaveLength(0)
    expect(document.querySelector('[data-testid="field-title"]')).toBeNull()
    type(field, `morgen 9 Uhr ${'x'.repeat(201)}`)
    expect(text('quick-preview')).toBe('Der Titel darf höchstens 200 Zeichen haben (jetzt 201).')
    await enter(field)
    expect(posts()).toHaveLength(0)
    expect(field.value).toContain('x'.repeat(201))
  })

  it('TIME-10 EC-6 lines pasted become one line and one event', async () => {
    const field = await open((method, path, body) => (method === 'POST' && path.startsWith('/api/events') ? saved(body) : undefined))
    const paste = new Event('paste', { bubbles: true, cancelable: true }) as Event & { clipboardData: unknown }
    paste.clipboardData = { getData: () => 'morgen 9 Uhr\nZahnarzt\r\nmit Ben' }
    await act(async () => void field.dispatchEvent(paste))
    expect(field.value).toBe('morgen 9 Uhr Zahnarzt mit Ben')
    await enter(field)
    expect(posts()).toHaveLength(1)
    expect((posts()[0].body as { title: string }).title).toBe('Zahnarzt mit Ben')
  })

  it('TIME-10 EC-10 Enter twice quickly makes one event', async () => {
    let answer: (value: Response) => void = () => undefined
    const field = await open()
    const original = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith('/api/events') && init?.method === 'POST') {
        calls.push({ method: 'POST', path: String(input), body: JSON.parse(String(init.body)), headers: {} })
        return new Promise<Response>((resolve) => (answer = resolve))
      }
      return original(input, init)
    }))
    type(field, 'morgen 9 Uhr Zahnarzt')
    await act(async () => {
      field.form!.requestSubmit()
      field.form!.requestSubmit()
    })
    expect(posts()).toHaveLength(1)
    await act(async () => answer(new Response(JSON.stringify({ id: 77, title: 'Zahnarzt' }), { status: 201, headers: { 'Content-Type': 'application/json' } })))
    await settle()
    expect(field.value).toBe('')
  })

  it('TIME-10 EC-11 the connection breaks: the text stays, a hint, and the retry carries the same reference', async () => {
    let broken = true
    const field = await open()
    const original = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith('/api/events') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body))
        calls.push({ method: 'POST', path: String(input), body, headers: {} })
        if (broken) throw new TypeError('Failed to fetch')
        return new Response(JSON.stringify({ id: 77, title: body.title }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      }
      return original(input, init)
    }))
    type(field, 'morgen 9 Uhr Zahnarzt')
    await enter(field)
    expect(field.value).toBe('morgen 9 Uhr Zahnarzt')
    expect(text('quick-failed')).toBe('Nicht gespeichert: Die Verbindung ist abgebrochen. Der Text bleibt, Eingabe versucht es noch einmal.')
    broken = false
    await enter(field)
    expect(field.value).toBe('')
    const refs = posts().map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs).toHaveLength(2)
    expect(refs[0]).toBe(refs[1])
  })

  it('TIME-10 EC-14 code in the title is text, in the preview too', async () => {
    const field = await open()
    type(field, 'morgen 9 Uhr <script>alert(1)</script><img src=x onerror=alert(1)>')
    expect(text('quick-title')).toBe('<script>alert(1)</script><img src=x onerror=alert(1)>')
    expect(box.querySelector('[data-testid="quick-preview"] script, [data-testid="quick-preview"] img')).toBeNull()
  })

  it('TIME-10 EC-15 on a phone the short placeholder, and the keyboard\'s key says done', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: true, media: query, addEventListener: () => undefined, removeEventListener: () => undefined }))
    const field = await open()
    expect(field.getAttribute('placeholder')).toBe('Schnell eintragen …')
    expect(field.getAttribute('enterkeyhint')).toBe('done')
  })
})

describe('TIME-10 the field after the review', () => {
  it('TIME-10 AC-5 TIME-13 EC-18 one calendar shown alone that may only be read: the preview says where it goes', async () => {
    setPref('only', 1, '4')
    const field = await open()
    type(field, 'morgen 9 Uhr Team')
    expect(text('quick-read-only')).toBe('„Anna · Arbeit“ ist nur lesbar; der Termin kommt in einen anderen Kalender.')
    expect(text('quick-when')).toContain('· Familie, dein Standard')
    type(field, 'morgen 9 Uhr Team #Arbeit')
    expect(one('quick-read-only')).toBeNull()
  })

  it('TIME-10 EC-11 another text is another event: the second entry carries another reference', async () => {
    const field = await open((method, path, body) => (method === 'POST' && path.startsWith('/api/events') ? saved(body) : undefined))
    type(field, 'morgen 9 Uhr Zahnarzt')
    await enter(field)
    type(field, 'morgen 10 Uhr Friseur')
    await enter(field)
    const refs = posts().map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs).toHaveLength(2)
    expect(refs[0]).not.toBe(refs[1])
  })

  it('TIME-10 EC-11 a refusal of the server says its own sentence, not a broken connection', async () => {
    const field = await open((method, path) =>
      method === 'POST' && path.startsWith('/api/events') ? { status: 403, body: { detail: { code: 'calendar_read_only', message: 'x', owner: 'Anna' } } } : undefined)
    type(field, 'morgen 9 Uhr Zahnarzt')
    await enter(field)
    expect(text('quick-failed')).not.toContain('Verbindung')
    expect(text('quick-failed')).toBe(i18n.t('errors.calendar_read_only', { owner: 'Anna' }))
    expect(field.value).toBe('morgen 9 Uhr Zahnarzt')
  })

  it('TIME-10 EC-13 the preview follows the clock over midnight, and Enter takes the date of that moment', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
    vi.setSystemTime(new Date('2026-11-26T22:59:40Z'))
    const field = await open((method, path, body) => (method === 'POST' && path.startsWith('/api/events') ? saved(body) : undefined))
    type(field, 'morgen 9 Uhr Arzt')
    expect(text('quick-when')).toContain('Fr, 27. November')
    await act(async () => void vi.advanceTimersByTime(30_000))
    expect(text('quick-when')).toContain('Sa, 28. November')
    vi.setSystemTime(new Date('2026-11-27T23:00:30Z'))
    await enter(field)
    expect((posts()[0].body as { start: string }).start).toBe('2026-11-29T09:00')
  })

  it('TIME-10 AC-4 a length without a time: the preview says so, Enter opens the event', async () => {
    const field = await open()
    type(field, 'morgen 2 Std Zahnarzt')
    expect(text('quick-preview')).toBe('Dauer ohne Uhrzeit erkannt. Eingabe öffnet den Termin.')
    await enter(field)
    expect(posts()).toHaveLength(0)
    expect((document.querySelector('[data-testid="field-title"]') as HTMLInputElement).value).toBe('Zahnarzt')
  })
})
