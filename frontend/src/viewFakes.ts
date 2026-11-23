/**
 * The made-up week of the screens for the tests of the views (no page imports this): today is Thursday,
 * 26 November 2026, 16:05 in Berlin (KW 48); the account Robin, the members Anna and Ben.
 */
import type { Display } from './api/client'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { CalendarList, Collection, Occurrence, Person } from './features/calendars/api'
import { instantOf } from './features/calendars/time'
import { me, ok, serve, signedIn, type Answer } from './testing'

export const NOW = new Date('2026-11-26T15:05:00Z')
export const BERLIN = { week_start: 1, week_numbers: true, clock: '24' as Display['clock'], time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' }
export const robin = me({ id: 1, name: 'robin', display_name: 'Robin', display: BERLIN })

export const anna: Person = { id: 2, name: 'Anna', color: 1, avatar: null }
export const ben: Person = { id: 3, name: 'Ben', color: 2, avatar: '2026-11-20T12:00:00+00:00' }

export const CALENDARS = calendarList([
  calendar(1, 'Familie', { default: true, shared: true }),
  calendar(2, 'Persönlich', { color: 'orange' }),
  calendar(3, 'Arbeit', { color: 'teal' }),
  calendar(4, 'Anna · Arbeit', { color: 'gray', group: 'shared', right: 'busy', writable: false, owner: 'Anna' }),
  calendar(5, 'Feiertage', { color: 'gray', background: true, holiday: true }),
  calendar(6, 'Müllabfuhr', { color: 'brown', background: true }),
  calendar(7, 'Verein', { color: 'green' }),
])

let next = 100

/** An occurrence at a wall time in Berlin (`2026-11-26 16:30`), or a whole day (`2026-11-29`). */
export function at(calendarId: number, title: string, start: string, end: string, change: Partial<Occurrence> = {}): Occurrence {
  const allDay = start.length === 10
  const instant = (text: string) => new Date(instantOf(text.slice(0, 10), text.slice(11, 16), 'Europe/Berlin')).toISOString().replace('.000Z', 'Z')
  next += 1
  return {
    id: next,
    occurrence: null,
    calendar_id: calendarId,
    title,
    location: '',
    busy: !allDay,
    busy_only: false,
    recurring: false,
    all_day: allDay,
    start: allDay ? start : instant(start),
    end: allDay ? end : instant(end),
    time_zone: allDay ? '' : 'Europe/Berlin',
    writable: calendarId !== 4,
    etag: `"e${next}"`,
    person: null,
    ...change,
  }
}

export type Fake = {
  rows: Occurrence[]
  calendars?: CalendarList
  collections?: Collection[]
  more?: (method: string, path: string, body: unknown) => Answer | undefined
}

/** The server for the views: calendars, collections and the occurrences of the window asked (only the calendars
 * asked for, as the server does). */
export function views(fake: Fake): void {
  serve(
    signedIn(robin, (method, path, body) => {
      const extra = fake.more?.(method, path, body)
      if (extra) return extra
      if (path.startsWith('/api/calendars')) return ok(fake.calendars ?? CALENDARS)
      if (path.startsWith('/api/collections')) return ok({ collections: fake.collections ?? [] })
      if (path.startsWith('/api/events?') && method === 'GET') {
        const query = new URL(path, 'http://x').searchParams
        const only = query.get('calendars')
        const start = query.get('start')!
        const end = query.get('end')!
        const chosen = only === null ? null : only.split(',').filter(Boolean).map(Number)
        return ok(
          fake.rows.filter((row) => {
            if (chosen && !chosen.includes(row.calendar_id)) return false
            if (row.all_day) return row.end >= start.slice(0, 10) && row.start <= end.slice(0, 10)
            return Date.parse(row.start) < Date.parse(end) && Date.parse(row.end) > Date.parse(start)
          }),
        )
      }
      if (path.startsWith('/api/search/upcoming') && method === 'GET') return ok(upcoming(fake, new URL(path, 'http://x').searchParams))
      const opened = /^\/api\/events\/(\d+)/.exec(path)
      if (opened && method === 'GET') {
        const row = fake.rows.find((entry) => entry.id === Number(opened[1]))
        return ok(eventDetail({ id: Number(opened[1]), calendar_id: row?.calendar_id ?? 1, title: row?.title ?? '', etag: row?.etag ?? '"e"' }))
      }
      return undefined
    }),
  )
}

/** The week of the screens: Thursday 26 November and the days after. */
export function theWeek(): Occurrence[] {
  return [
    at(6, 'Gelbe Tonne', '2026-11-26', '2026-11-26'),
    at(3, 'Teamrunde', '2026-11-26 09:30', '2026-11-26 10:30', { location: 'Raum Nord' }),
    at(2, 'Mittag mit Chris', '2026-11-26 12:30', '2026-11-26 13:15'),
    at(4, '', '2026-11-26 14:00', '2026-11-26 16:00', { busy_only: true, person: anna }),
    at(1, 'Zahnarzt Ben', '2026-11-26 16:30', '2026-11-26 17:15', { location: 'Praxis Dr. Weber, Lindenstraße 4', person: anna }),
    at(1, 'Telefonat mit Oma', '2026-11-26 20:30', '2026-11-26 21:00'),
    at(2, 'Werkstatt', '2026-11-27 08:15', '2026-11-27 09:00', { location: 'Autohaus Süd' }),
    at(1, 'Fußball Ben', '2026-11-27 17:00', '2026-11-27 18:00', { person: ben }),
    at(7, 'Wochenmarkt-Stand', '2026-11-28 10:00', '2026-11-28 12:00'),
    at(5, '1. Advent', '2026-11-29', '2026-11-29'),
    at(1, 'Kaffee bei Oma', '2026-11-29 15:00', '2026-11-29 17:00'),
    at(4, '', '2026-12-03 10:00', '2026-12-03 12:00', { busy_only: true, person: anna }),
  ]
}

/** What `/api/search/upcoming` answers from the rows of the fake: coming ones by start, from a day on, without
 * background calendars when asked so, a page at a time (the column and "Nächste Termine", TIME-11, TIME-13). */
function upcoming(fake: Fake, query: URLSearchParams) {
  const calendars = (fake.calendars ?? CALENDARS).calendars
  const now = Date.parse(query.get('now') ?? new Date().toISOString())
  const from = query.get('from')
  const only = query.get('calendars')
  const chosen = only === null ? null : only.split(',').filter(Boolean).map(Number)
  const dayOf = (row: Occurrence) => (row.all_day ? row.start : new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Berlin' }).format(new Date(row.start)))
  const coming = fake.rows
    .filter((row) => (row.all_day ? `${row.end}T23:59:59Z` > new Date(now).toISOString() : Date.parse(row.end) > now))
    .filter((row) => !chosen || chosen.includes(row.calendar_id))
    .filter((row) => !calendars.find((entry) => entry.id === row.calendar_id)?.hidden)
    .filter((row) => query.get('background') !== 'false' || !calendars.find((entry) => entry.id === row.calendar_id)?.background)
    .filter((row) => !from || dayOf(row) >= from)
    .sort((a, b) => (a.all_day ? `${a.start}T00:00:00Z` : a.start).localeCompare(b.all_day ? `${b.start}T00:00:00Z` : b.start))
  const offset = Number(query.get('offset') ?? 0)
  const limit = Number(query.get('limit') ?? 50)
  const items = coming.slice(offset, offset + limit).map((row) => ({
    id: row.id, occurrence: row.occurrence, calendar_id: row.calendar_id, title: row.busy_only ? '' : row.title, all_day: row.all_day, start: row.start, end: row.end,
    time_zone: row.time_zone ?? '', recurring: row.recurring, coming: true, location: row.busy_only ? '' : row.location ?? '', busy_only: row.busy_only, rrule: '', invitation: row.invitation ?? null,
  }))
  return { total: coming.length, future_total: coming.length, offset, items }
}
