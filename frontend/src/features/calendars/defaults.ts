/**
 * "Neue Termine in diesem Kalender" (TIME-4, decided while testing, 2026-11-23: birthdays are always yearly): what
 * a new event of a calendar starts with, per person, kept by the server beside the calendar (`defaults` of
 * `/api/calendars`). A key left out is as from the start: an hour, no repeat, the calendar's reminder, busy unless
 * whole-day. What the member gives himself wins: a time dragged or typed, "jede Woche" or "2h" in the quick field.
 */
import type { CalendarEntry, Rule, Weekday } from './api'

export type RuleChoice = '' | 'daily' | 'weekly' | 'biweekly' | 'monthly' | 'yearly'

export type NewDefaults = {
  all_day?: boolean
  /** The length of an event with a time, in minutes. */
  minutes?: number
  rule?: RuleChoice
  /** Lead times of new events with a time. */
  reminders?: number[]
  /** Lead times of new whole-day events (360 the day before at 18:00, -540 on the day at 09:00). */
  reminders_all_day?: number[]
  busy?: boolean
}

export const DURATIONS = [15, 30, 45, 60, 90, 120, 180] as const
export const RULE_CHOICES: RuleChoice[] = ['', 'daily', 'weekly', 'biweekly', 'monthly', 'yearly']
/** The usual length of a new event with a time (TIME-5 AC-10). */
export const USUAL_MINUTES = 60

export function defaultsOf(calendar: CalendarEntry | null | undefined): NewDefaults {
  return calendar?.defaults ?? {}
}

/** The repeat of a choice, without end: weekly on the weekday of the start, yearly on its day ("Jedes Jahr am 9. November"). */
export function ruleOf(choice: RuleChoice | undefined, first?: string): Rule | null {
  if (!choice) return null
  const freq = choice === 'biweekly' ? 'weekly' : choice
  // Weekly on the weekday of the first event, as the repeat dialog fills it in.
  const weekdays: Weekday[] = freq === 'weekly' && first ? [CODES[new Date(`${first}T00:00:00Z`).getUTCDay()]] : []
  return { freq, interval: choice === 'biweekly' ? 2 : 1, weekdays, monthly: 'date', end: 'never', until: null, count: null }
}

const CODES: Weekday[] = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA']

/** What the member chose for an unsaved new event himself, and what came with it from where it was opened. */
export type Touched = { allDay: boolean; times: boolean; rule: boolean; busy: boolean; reminders: boolean }

export type Fields = {
  allDay: boolean
  busy: boolean
  startDate: string
  startTime: string
  endDate: string
  endTime: string
  rule: Rule | null
  reminders: number[]
  /** The reminders came from these defaults and go with the event when saved. */
  remindersFromDefaults: boolean
}

function plus(date: string, time: string, minutes: number): { date: string; time: string } {
  const [year, month, day] = date.split('-').map(Number)
  const [hours, mins] = time.split(':').map(Number)
  const at = new Date(Date.UTC(year, month - 1, day, hours, mins + minutes))
  return { date: at.toISOString().slice(0, 10), time: at.toISOString().slice(11, 16) }
}

/**
 * The reminders of the defaults for an event of this kind (decided after the review, 2026-11-23): a whole day and an
 * event with a time have their own; one chosen for the one never goes to the other. Stored before that, the reminders
 * of a calendar whose new events are whole days are those of whole days.
 */
export function remindersFor(chosen: NewDefaults, allDay: boolean): number[] | undefined {
  const legacyAllDay = chosen.all_day === true && chosen.reminders_all_day === undefined
  if (allDay) return chosen.reminders_all_day ?? (legacyAllDay ? chosen.reminders : undefined)
  return legacyAllDay ? undefined : chosen.reminders
}

/** `fields` with the defaults of `calendar` for everything not `touched` (a new event, or its calendar changed). */
export function applyDefaults<T extends Fields>(fields: T, calendar: CalendarEntry | null | undefined, touched: Touched): T {
  const chosen = defaultsOf(calendar)
  const next = { ...fields }
  if (!touched.allDay && !touched.times) {
    next.allDay = chosen.all_day ?? false
    if (next.allDay) next.endDate = next.startDate
  }
  if (!touched.times && !next.allDay) {
    const end = plus(next.startDate, next.startTime, chosen.minutes ?? USUAL_MINUTES)
    next.endDate = end.date
    next.endTime = end.time
  }
  if (!touched.rule) next.rule = ruleOf(chosen.rule, next.startDate)
  if (!touched.busy) next.busy = chosen.busy ?? !next.allDay
  if (!touched.reminders) {
    // The kind the event has now: a click into the all-day row is a whole day even when the defaults say otherwise.
    const found = remindersFor(chosen, next.allDay)
    next.reminders = found ?? []
    next.remindersFromDefaults = found !== undefined
  }
  return next
}
