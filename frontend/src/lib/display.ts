/**
 * How the account sees its times (TIME-3): first day of the week, ISO week numbers, 12 or 24 hours, and the time zone
 * the times stand in. The choices belong to the account and travel to every device (`Me.display`); only light or dark
 * stays per device (`lib/theme.ts`).
 *
 * Times are formatted with `Intl` in the zone shown, so a night the clocks change, a zone with half hours (Kolkata) or
 * one without summer time comes out right without arithmetic of our own (EC-1, EC-9).
 */
import type { Display } from '../api/client'

export const DEFAULT_DISPLAY: Display = { week_start: 1, week_numbers: true, clock: 'auto', time_zone: '', home_zone: '' }

const DAY_MS = 86_400_000

/** The zone the times stand in: the one the account chose, else the device's (AC-5, AC-6). */
export function shownZone(display: Display, device: string): string {
  return display.time_zone || device || 'UTC'
}

/** 12 hours or 24: as chosen, else after the language (German 24, English 12; an added one as its language does). */
export function uses12Hours(display: Display, language: string): boolean {
  if (display.clock === '12') return true
  if (display.clock === '24') return false
  if (language.startsWith('de')) return false
  if (language.startsWith('en')) return true
  try {
    const cycle = new Intl.DateTimeFormat(language, { hour: 'numeric' }).resolvedOptions().hourCycle
    return cycle === 'h12' || cycle === 'h11'
  } catch {
    return false
  }
}

/** A time of day: "18:30" in 24 hours, "6:30 PM" in 12, midnight "12:00 AM" and noon "12:00 PM" (AC-8, EC-8). */
export function formatTime(value: Date | number | string, options: { language: string; twelve: boolean; zone: string }): string {
  return new Intl.DateTimeFormat(options.language, {
    hour: options.twelve ? 'numeric' : '2-digit',
    minute: '2-digit',
    hourCycle: options.twelve ? 'h12' : 'h23',
    timeZone: options.zone,
  }).format(new Date(value))
}

/** The parts of a date in a zone: year, month (1–12), day, weekday (0 Sunday … 6 Saturday). */
export function dateIn(value: Date | number, zone: string): { year: number; month: number; day: number; weekday: number } {
  const parts = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'numeric', day: 'numeric', weekday: 'short', timeZone: zone }).formatToParts(new Date(value))
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? ''
  const weekday = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(get('weekday'))
  return { year: Number(get('year')), month: Number(get('month')), day: Number(get('day')), weekday }
}

/** A calendar day as a plain value (no time, no zone): days of the grid are counted on these. */
export type Day = { year: number; month: number; day: number }

function utcOf(day: Day): number {
  return Date.UTC(day.year, day.month - 1, day.day)
}

export function addDays(day: Day, count: number): Day {
  const moved = new Date(utcOf(day) + count * DAY_MS)
  return { year: moved.getUTCFullYear(), month: moved.getUTCMonth() + 1, day: moved.getUTCDate() }
}

export function weekdayOf(day: Day): number {
  return new Date(utcOf(day)).getUTCDay()
}

/** The ISO week of a day: the week with the year's first Thursday is week 1. */
export function isoWeek(day: Day): number {
  const date = new Date(utcOf(day))
  // The Thursday of the day's week (weeks run Monday to Sunday) decides the year and the number.
  const thursday = new Date(date.getTime() + (3 - ((date.getUTCDay() + 6) % 7)) * DAY_MS)
  const january4 = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 4))
  const firstThursday = new Date(january4.getTime() + (3 - ((january4.getUTCDay() + 6) % 7)) * DAY_MS)
  return 1 + Math.round((thursday.getTime() - firstThursday.getTime()) / (7 * DAY_MS))
}

/** The first day of the week that holds `day`, for a week starting on `weekStart` (0 Sunday … 6 Saturday). */
export function startOfWeek(day: Day, weekStart: number): Day {
  return addDays(day, -((weekdayOf(day) - weekStart + 7) % 7))
}

/** The number shown for a week that starts on `first`: always ISO, the week its Thursday lies in (EC-13). */
export function weekNumber(first: Day): number {
  const toThursday = (4 - weekdayOf(first) + 7) % 7
  return isoWeek(addDays(first, toThursday))
}

/** The seven days of the week that holds `day`. */
export function weekDays(day: Day, weekStart: number): Day[] {
  const first = startOfWeek(day, weekStart)
  return Array.from({ length: 7 }, (_, index) => addDays(first, index))
}

/** The weeks of a month's grid: from the week of the 1st to the week of its last day, seven days each. */
export function monthWeeks(year: number, month: number, weekStart: number): Day[][] {
  const first: Day = { year, month, day: 1 }
  const last = addDays({ year: month === 12 ? year + 1 : year, month: month === 12 ? 1 : month + 1, day: 1 }, -1)
  const weeks: Day[][] = []
  let start = startOfWeek(first, weekStart)
  while (utcOf(start) <= utcOf(last)) {
    weeks.push(Array.from({ length: 7 }, (_, index) => addDays(start, index)))
    start = addDays(start, 7)
  }
  return weeks
}

/** The names of the weekdays in the order of the week, short ("Mo") or long. */
export function weekdayNames(weekStart: number, language: string, width: 'short' | 'long' = 'short'): string[] {
  // 2026-11-01 was a Sunday.
  const format = new Intl.DateTimeFormat(language, { weekday: width, timeZone: 'UTC' })
  return Array.from({ length: 7 }, (_, index) => format.format(new Date(Date.UTC(2026, 10, 1 + ((weekStart + index) % 7)))))
}

/** A day written out in the language: "Donnerstag, 26. November 2026" or "Thursday, November 26, 2026". */
export function longDay(day: Day, language: string): string {
  return new Intl.DateTimeFormat(language, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(utcOf(day)))
}

/** A month and its year: "November 2026". */
export function monthTitle(year: number, month: number, language: string): string {
  return new Intl.DateTimeFormat(language, { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, 1)))
}

/** A short day in the grid's head: "26.11." or "11/26". */
export function shortDay(day: Day, language: string): string {
  return new Intl.DateTimeFormat(language, { day: 'numeric', month: 'numeric', timeZone: 'UTC' }).format(new Date(utcOf(day)))
}

/** The offset of a zone at a moment, as people write it: "UTC−5", "UTC+5:30", "UTC" (minus as U+2212). */
export function utcOffset(zone: string, at: Date | number = Date.now()): string {
  let minutes = 0
  try {
    const name = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'longOffset' }).formatToParts(new Date(at)).find((part) => part.type === 'timeZoneName')?.value ?? 'GMT'
    const found = /GMT([+-])(\d{1,2})(?::?(\d{2}))?/.exec(name)
    if (found) minutes = (found[1] === '-' ? -1 : 1) * (Number(found[2]) * 60 + Number(found[3] ?? 0))
  } catch {
    return 'UTC'
  }
  if (minutes === 0) return 'UTC'
  const sign = minutes < 0 ? '−' : '+'
  const hours = Math.floor(Math.abs(minutes) / 60)
  const rest = Math.abs(minutes) % 60
  return `UTC${sign}${hours}${rest ? `:${String(rest).padStart(2, '0')}` : ''}`
}

/** The place of a zone as people say it: "New York" for America/New_York, "Berlin" for Europe/Berlin. */
export function zonePlace(zone: string): string {
  const last = zone.split('/').pop() ?? zone
  return last.replace(/_/g, ' ')
}

/**
 * The hint on top while travelling (AC-5, AC-11): only when the zone shown differs from the home zone, which the
 * first sign-in set. Null otherwise, and while no home zone is known yet.
 */
export function travelHint(display: Display, device: string, at: Date | number = Date.now()): { place: string; offset: string } | null {
  const shown = shownZone(display, device)
  if (!display.home_zone || shown === display.home_zone) return null
  // Two names of one zone (Asia/Calcutta and Asia/Kolkata) are no journey.
  if (utcOffset(shown, at) === utcOffset(display.home_zone, at) && zonePlace(shown) === zonePlace(display.home_zone)) return null
  return { place: zonePlace(shown), offset: utcOffset(shown, at) }
}

/** The zones to choose from: what the browser knows, else a short list. */
export function knownZones(): string[] {
  try {
    const listed = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.('timeZone')
    if (listed && listed.length > 0) return listed
  } catch {
    // An old browser.
  }
  return ['UTC', 'Europe/Berlin', 'Europe/London', 'Europe/Vienna', 'Europe/Zurich', 'America/New_York', 'America/Los_Angeles', 'Asia/Kolkata', 'Asia/Tokyo', 'Australia/Sydney']
}

/**
 * A change of the clocks on `day` in `zone` (EC-1): the hour the clocks jump from and to, so a view can say
 * "02:00 → 03:00". Null on an ordinary day.
 */
export function clockChange(day: Day, zone: string): { from: string; to: string } | null {
  const offsetAt = (ms: number) => {
    const name = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'longOffset' }).formatToParts(new Date(ms)).find((part) => part.type === 'timeZoneName')?.value ?? 'GMT'
    const found = /GMT([+-])(\d{1,2})(?::?(\d{2}))?/.exec(name)
    return found ? (found[1] === '-' ? -1 : 1) * (Number(found[2]) * 60 + Number(found[3] ?? 0)) : 0
  }
  // The day's span in UTC, with room for any offset.
  const start = utcOf(day) - 14 * 3_600_000
  let before = offsetAt(start)
  for (let ms = start + 15 * 60_000; ms <= utcOf(day) + 38 * 3_600_000; ms += 15 * 60_000) {
    const now = offsetAt(ms)
    if (now !== before) {
      const local = dateIn(ms, zone)
      if (local.year === day.year && local.month === day.month && local.day === day.day) {
        const wall = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
        const localMinutes = Math.round(((ms + now * 60_000) % DAY_MS) / 60_000)
        const to = (localMinutes + 24 * 60) % (24 * 60)
        const from = (to - (now - before) + 24 * 60) % (24 * 60)
        return { from: wall(from), to: wall(to) }
      }
      before = now
    }
  }
  return null
}

/** Today in the zone shown. */
export function today(zone: string, now: Date | number = Date.now()): Day {
  const { year, month, day } = dateIn(now, zone)
  return { year, month, day }
}
