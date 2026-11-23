/**
 * Where the occurrences stand, as plain functions without the page: which days they touch in the zone shown, their
 * place in the grid of a day (hours from midnight on the wall clock, so the day the clocks change stays right,
 * TIME-12 EC-11), side by side when they overlap (at most three, the rest as "+n", AC-4), and whole days as bars over
 * the days they last (EC-5).
 */
import type { CalendarEntry, Occurrence } from '../calendars/api'
import { instantOf, wallOf } from '../calendars/time'
import { addDays, daysBetween } from './dates'

export type Part = {
  row: Occurrence
  date: string
  /** Hours from the start of `date` on the wall clock: 7.5 is half past seven. */
  from: number
  to: number
  /** It began on a day before (over midnight, EC-1). */
  before: boolean
  /** It goes on into the next day. */
  after: boolean
}

export function hoursOf(time: string): number {
  const [hour, minute] = time.split(':').map(Number)
  return hour + minute / 60
}

/** The first and the last day an occurrence touches in the zone shown. */
export function daysOf(row: Occurrence, zone: string): { first: string; last: string } {
  if (row.all_day) return { first: row.start, last: row.end }
  const start = Date.parse(row.start)
  const end = Date.parse(row.end)
  const first = wallOf(start, zone).date
  const ending = wallOf(end, zone)
  // An event ending at midnight ends on the day before.
  const last = end > start && ending.time === '00:00' ? addDays(ending.date, -1) : ending.date
  return { first, last: last < first ? first : last }
}

export function touches(row: Occurrence, date: string, zone: string): boolean {
  const { first, last } = daysOf(row, zone)
  return first <= date && date <= last
}

/**
 * A long event: whole days over more than one day, or times lasting 24 hours or more over several days. It stands
 * once as running in Heute (TIME-11 EC-2) and as a bar over its days in Day and Week (TIME-12 EC-5). An evening that
 * ends after midnight ("Kino 21:30–00:15") is not long: it comes next, runs and goes past like any other.
 */
export function multiDay(row: Occurrence, zone: string): boolean {
  const { first, last } = daysOf(row, zone)
  if (last <= first) return false
  return row.all_day || Date.parse(row.end) - Date.parse(row.start) >= 86_400_000
}

/** "Day 3 of 7" of a long event on `date`. */
export function dayOf(row: Occurrence, date: string, zone: string): { day: number; of: number } {
  const { first, last } = daysOf(row, zone)
  return { day: daysBetween(first, date) + 1, of: daysBetween(first, last) + 1 }
}

export function isBackground(row: Occurrence, calendars: Map<number, CalendarEntry>): boolean {
  return !!calendars.get(row.calendar_id)?.background
}

/** Whether one of `rows` is a public holiday, so the day stands out (TIME-17, decided while testing). */
export function isHoliday(rows: Occurrence[], calendars: Map<number, CalendarEntry>): boolean {
  return rows.some((row) => !!calendars.get(row.calendar_id)?.holiday)
}

/** The pieces of the timed occurrences on each of `dates`: an event over midnight on both days (EC-1). */
export function timedParts(rows: Occurrence[], dates: string[], zone: string): Map<string, Part[]> {
  const parts = new Map<string, Part[]>(dates.map((date) => [date, []]))
  for (const row of rows) {
    if (row.all_day) continue
    const start = wallOf(Date.parse(row.start), zone)
    const end = wallOf(Date.parse(row.end), zone)
    const { first, last } = daysOf(row, zone)
    for (const date of dates) {
      if (date < first || date > last) continue
      const from = date === start.date ? hoursOf(start.time) : 0
      const to = date === end.date ? hoursOf(end.time) : 24
      parts.get(date)!.push({ row, date, from, to: Math.max(to, from), before: date > start.date, after: date < last })
    }
  }
  for (const list of parts.values()) list.sort((a, b) => a.from - b.from || b.to - a.to)
  return parts
}

export type Column = { part: Part; column: number; columns: number }
export type More = { from: number; parts: Part[] }

/** Overlapping pieces side by side: groups of pieces that overlap, each in the first free column; more than `max`
 * columns show the first `max` and "+n" for the rest (TIME-12 AC-4). */
export function columns(parts: Part[], max = 3): { placed: Column[]; more: More[] } {
  const placed: Column[] = []
  const more: More[] = []
  let group: { part: Part; column: number }[] = []
  let groupEnd = -1
  const close = () => {
    if (!group.length) return
    const ends: number[] = []
    for (const entry of group) {
      let column = ends.findIndex((end) => end <= entry.part.from + 1e-9)
      if (column < 0) {
        column = ends.length
        ends.push(0)
      }
      ends[column] = Math.max(entry.part.to, entry.part.from + 0.25)
      entry.column = column
    }
    const count = Math.min(max, ends.length)
    const hidden = group.filter((entry) => entry.column >= max).map((entry) => entry.part)
    for (const entry of group) if (entry.column < max) placed.push({ part: entry.part, column: entry.column, columns: count })
    if (hidden.length) more.push({ from: group[0].part.from, parts: hidden })
    group = []
    groupEnd = -1
  }
  for (const part of parts) {
    const end = Math.max(part.to, part.from + 0.25)
    if (group.length && part.from >= groupEnd - 1e-9) close()
    group.push({ part, column: 0 })
    groupEnd = Math.max(groupEnd, end)
  }
  close()
  return { placed, more }
}

export type Bar = { row: Occurrence; first: number; last: number; lane: number; cutStart: boolean; cutEnd: boolean }

/**
 * Whole days and long events in the row over the grid: one bar from the first to the last of `dates` it covers (EC-5),
 * in the first lane free on all its days.
 */
export function bars(rows: Occurrence[], dates: string[], zone: string): Bar[] {
  const found: Bar[] = []
  const taken: boolean[][] = []
  const sorted = [...rows].sort((a, b) => {
    const da = daysOf(a, zone)
    const db = daysOf(b, zone)
    return da.first.localeCompare(db.first) || db.last.localeCompare(da.last) || a.title.localeCompare(b.title)
  })
  for (const row of sorted) {
    const { first, last } = daysOf(row, zone)
    const from = dates.findIndex((date) => date >= first)
    let to = -1
    for (let index = dates.length - 1; index >= 0; index--) if (dates[index] <= last) {
      to = index
      break
    }
    if (from < 0 || to < from) continue
    let lane = 0
    while (Array.from({ length: to - from + 1 }, (_, step) => taken[lane]?.[from + step]).some(Boolean)) lane++
    taken[lane] = taken[lane] ?? []
    for (let index = from; index <= to; index++) taken[lane][index] = true
    found.push({ row, first: from, last: to, lane, cutStart: first < dates[from], cutEnd: last > dates[to] })
  }
  return found
}

/** A quarter of an hour: dragging moves and stretches in these steps (TIME-12 AC-2). */
export function quarter(hours: number): number {
  return Math.round(hours * 4) / 4
}

/**
 * Where a dragged event ends up: moved by `minutes` and `days` on the wall clock of the zone shown (a move over the
 * change of the clocks keeps 18:30 at 18:30), or, with `stretch`, only its end moved, at least a quarter of an hour.
 * The result as wall times in the event's own zone, as the server takes them.
 */
export function moved(row: Occurrence, zone: string, minutes: number, days: number, stretch: boolean): { start: string; end: string; time_zone: string } {
  const own = row.time_zone || zone
  const start = Date.parse(row.start)
  const end = Date.parse(row.end)
  const shift = (ms: number) => {
    const wall = wallOf(ms, zone)
    const total = hoursOf(wall.time) * 60 + minutes
    const date = addDays(wall.date, days + Math.floor(total / 1440))
    const within = ((total % 1440) + 1440) % 1440
    return instantOf(date, `${String(Math.floor(within / 60)).padStart(2, '0')}:${String(within % 60).padStart(2, '0')}`, zone)
  }
  const newStart = stretch ? start : shift(start)
  const newEnd = stretch ? Math.max(shift(end), start + 15 * 60_000) : newStart + (end - start)
  const a = wallOf(newStart, own)
  const b = wallOf(newEnd, own)
  return { start: `${a.date}T${a.time}`, end: `${b.date}T${b.time}`, time_zone: own }
}
