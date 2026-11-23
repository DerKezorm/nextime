/** Made-up calendars and events for the tests of the calendar pages (no page imports this). */
import type { CalendarEntry, CalendarList, EventDetail } from './features/calendars/api'

export function calendar(id: number, name: string, change: Partial<CalendarEntry> = {}): CalendarEntry {
  return {
    id,
    name,
    color: 'slate',
    background: false,
    reminder: 0,
    group: 'own',
    source: 'nextime',
    right: 'owner',
    writable: true,
    hidden: false,
    position: id,
    default: false,
    owner: null,
    shared: false,
    events: 0,
    ...change,
  }
}

export function calendarList(calendars: CalendarEntry[], change: Partial<CalendarList> = {}): CalendarList {
  const chosen = calendars.find((entry) => entry.default) ?? calendars.find((entry) => entry.writable)
  return {
    calendars,
    default_id: chosen?.id ?? null,
    default_moved: null,
    default_was: null,
    count: calendars.length,
    smooth_up_to: 20,
    reminders: [0, 5, 15, 30, 60, 1440],
    ...change,
  }
}

export function eventDetail(change: Partial<EventDetail> = {}): EventDetail {
  return {
    id: 7,
    etag: '"e1"',
    calendar_id: 1,
    occurrence: null,
    first: false,
    title: 'Training',
    notes: '',
    location: '',
    busy: true,
    all_day: false,
    start: '2026-11-26T18:30',
    end: '2026-11-26T20:00',
    time_zone: 'Europe/Berlin',
    recurring: false,
    rule: null,
    rrule: '',
    series_start: { all_day: false, start: '2026-11-26T18:30', end: '2026-11-26T20:00', time_zone: 'Europe/Berlin' },
    right: 'owner',
    writable: true,
    busy_only: false,
    created_by: { id: 1, name: 'Anna', color: 0, avatar: null },
    updated_by: { id: 1, name: 'Anna', color: 0, avatar: null },
    updated_at: '2026-11-26T15:00:00Z',
    shared: false,
    conflict: null,
    ...change,
  }
}
