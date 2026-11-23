/**
 * Days as the views count them: plain `YYYY-MM-DD` strings without zone, the weeks after the first day the account
 * chose (TIME-3 AC-2), calendar weeks after ISO (TIME-12 AC-15: the week with the year's first Thursday is week 1).
 */
import { addDays } from '../calendars/time'

export { addDays }

const DAY_MS = 86_400_000

function utc(date: string): number {
  const [year, month, day] = date.split('-').map(Number)
  return Date.UTC(year, month - 1, day)
}

function text(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

/** 0 Sunday … 6 Saturday. */
export function weekday(date: string): number {
  return new Date(utc(date)).getUTCDay()
}

export function isWeekend(date: string): boolean {
  const day = weekday(date)
  return day === 0 || day === 6
}

export function daysBetween(from: string, to: string): number {
  return Math.round((utc(to) - utc(from)) / DAY_MS)
}

/** The first day of the week holding `date`, for weeks that begin on `weekStart`. */
export function weekStartOf(date: string, weekStart: number): string {
  return addDays(date, -((weekday(date) - weekStart + 7) % 7))
}

export function range(first: string, count: number): string[] {
  return Array.from({ length: count }, (_, index) => addDays(first, index))
}

/** The ISO week of a day and the year it belongs to (KW 53 2026 holds 1 January 2027, EC-16). */
export function isoWeekOf(date: string): { week: number; year: number } {
  const thursday = addDays(date, 3 - ((weekday(date) + 6) % 7))
  const year = Number(thursday.slice(0, 4))
  const firstThursday = addDays(`${year}-01-04`, 3 - ((weekday(`${year}-01-04`) + 6) % 7))
  return { week: 1 + daysBetween(firstThursday, thursday) / 7, year }
}

/** The Monday of ISO week `week` of `year`. */
export function mondayOfWeek(week: number, year: number): string {
  const january4 = `${year}-01-04`
  return addDays(addDays(january4, -((weekday(january4) + 6) % 7)), (week - 1) * 7)
}

/** 52 or 53: the ISO weeks of a year (28 December always lies in its last week). */
export function weeksInYear(year: number): number {
  return isoWeekOf(`${year}-12-28`).week
}

/** The weeks of the month grid holding `date`: from the week of the 1st to the week of the last day (EC-12). */
export function monthWeeks(date: string, weekStart: number): string[][] {
  const first = `${date.slice(0, 7)}-01`
  const [year, month] = first.split('-').map(Number)
  const last = text(Date.UTC(year, month, 0))
  const start = weekStartOf(first, weekStart)
  const weeks: string[][] = []
  for (let day = start; day <= last; day = addDays(day, 7)) weeks.push(range(day, 7))
  return weeks
}

export function addMonths(date: string, months: number): string {
  const [year, month] = date.split('-').map(Number)
  return text(Date.UTC(year, month - 1 + months, 1))
}

/** "Do, 26. Nov" and the like, from parts `Intl` gives for the language. */
export function format(date: string, language: string, options: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat(language, { ...options, timeZone: 'UTC' }).format(new Date(utc(date)))
}

/** The rule of a series in a word: "täglich", "wöchentlich", "alle 2 Wochen", "monatlich", "jährlich". */
export function shortRule(rrule: string, t: (key: string, values?: Record<string, unknown>) => string): string {
  const parts = Object.fromEntries(rrule.split(';').map((part) => part.split('=') as [string, string]))
  const every = Number(parts.INTERVAL ?? 1) || 1
  const freq = (parts.FREQ ?? '').toLowerCase()
  if (!['daily', 'weekly', 'monthly', 'yearly'].includes(freq)) return ''
  return every > 1 ? t(`views.column.every.${freq}`, { count: every }) : t(`views.column.rules.${freq}`)
}
