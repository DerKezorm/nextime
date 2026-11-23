/**
 * The rule of a series as the dialog "Repeat" edits it (TIME-6): its sentence to check it by ("Every Monday and
 * Thursday, 18:30–20:00", AC-4), its checks (EC-8), and the sentence for a rule from elsewhere that nextime does not
 * offer itself (EC-9).
 */
import type { TFunction } from 'i18next'

import type { Rule, Weekday } from './api'
import { clockText, shortDate } from './time'

export const WEEKDAYS: Weekday[] = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU']

/** The weekday of a date, as the rule names it. */
export function weekdayOf(date: string): Weekday {
  const [year, month, day] = date.split('-').map(Number)
  return WEEKDAYS[(new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7]
}

/** Which of its weekday in the month a date is: 1 to 5. */
export function nthOf(date: string): number {
  return Math.floor((Number(date.slice(8, 10)) - 1) / 7) + 1
}

/** The last of its weekday in its month (no week left after it). */
export function isLastOf(date: string): boolean {
  const [year, month, day] = date.split('-').map(Number)
  return day + 7 > new Date(Date.UTC(year, month, 0)).getUTCDate()
}

export function weekdayName(day: Weekday, language: string, width: 'long' | 'short' = 'long'): string {
  // 2024-01-01 was a Monday.
  return new Intl.DateTimeFormat(language, { weekday: width, timeZone: 'UTC' }).format(new Date(Date.UTC(2024, 0, 1 + WEEKDAYS.indexOf(day))))
}

function list(items: string[], language: string): string {
  try {
    return new Intl.ListFormat(language, { style: 'long', type: 'conjunction' }).format(items)
  } catch {
    return items.join(', ')
  }
}

function dayOrdinal(day: number, language: string): string {
  if (!language.startsWith('en')) return `${day}.`
  const tens = day % 100
  const suffix = tens >= 11 && tens <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[day % 10] ?? 'th'
  return `${day}${suffix}`
}

/** The day in the month a monthly series meets: "26.", "2. Dienstag", "letzten Freitag" (a fifth weekday is the last,
 * EC-4). */
export function monthlyWhat(kind: Rule['monthly'], first: string, t: TFunction, language: string): string {
  const weekday = weekdayName(weekdayOf(first), language)
  if (kind === 'last' || (kind === 'nth' && nthOf(first) === 5)) return t('rule.nthWeekday', { nth: t('rule.ordinal.last'), weekday })
  if (kind === 'nth') return t('rule.nthWeekday', { nth: t(`rule.ordinal.${nthOf(first)}`), weekday })
  return dayOrdinal(Number(first.slice(8, 10)), language)
}

/** The rule a new series starts with: weekly on the day of its start. */
export function defaultRule(date: string): Rule {
  return { freq: 'weekly', interval: 1, weekdays: [weekdayOf(date)], monthly: 'date', end: 'never', until: null, count: null }
}

export type Times = { allDay: boolean; start: string; end: string } | null

/** "Jeden Montag und Donnerstag, 18:30–20:00, bis 26.11.2027" (AC-4). */
export function ruleSentence(rule: Rule, first: string, times: Times, t: TFunction, language: string, twelve = false): string {
  const count = Math.max(1, Number(rule.interval) || 1)
  let base: string
  if (rule.freq === 'daily') base = t('rule.daily', { count })
  else if (rule.freq === 'weekly') {
    const days = WEEKDAYS.filter((day) => rule.weekdays.includes(day)).map((day) => weekdayName(day, language))
    base = t('rule.weekly', { count, days: days.length ? list(days, language) : '…' })
  } else if (rule.freq === 'monthly') {
    base = t('rule.monthlyOn', { count, what: monthlyWhat(rule.monthly, first, t, language) })
  } else {
    const [year, month, day] = first.split('-').map(Number)
    const date = new Intl.DateTimeFormat(language, { day: 'numeric', month: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, day)))
    base = t('rule.yearly', { count, date })
  }
  const parts = [base]
  if (times) parts.push(times.allDay ? t('rule.allDay') : `${clockText(times.start, language, twelve)}–${clockText(times.end, language, twelve)}`)
  if (rule.end === 'until' && rule.until) parts.push(t('rule.until', { date: shortDate(rule.until, language) }))
  if (rule.end === 'count' && rule.count) parts.push(t('rule.count', { count: Number(rule.count) }))
  return parts.join(', ')
}

export type RuleProblems = Partial<Record<'interval' | 'weekdays' | 'until' | 'count', string>>

/** What is wrong with a rule, at its field (EC-8); empty when it can be taken. */
export function ruleProblems(rule: { interval: string | number; weekdays: Weekday[]; freq: Rule['freq']; end: Rule['end']; until: string | null; count: string | number | null }, first: string, t: TFunction): RuleProblems {
  const problems: RuleProblems = {}
  if (!/^\d+$/.test(String(rule.interval).trim()) || Number(rule.interval) < 1 || Number(rule.interval) > 999) problems.interval = t('rule.problem.interval')
  if (rule.freq === 'weekly' && rule.weekdays.length === 0) problems.weekdays = t('rule.problem.weekdays')
  if (rule.end === 'until' && (!rule.until || rule.until < first)) problems.until = t('rule.problem.until')
  if (rule.end === 'count' && (!/^\d+$/.test(String(rule.count ?? '').trim()) || Number(rule.count) < 1 || Number(rule.count) > 9999)) problems.count = t('rule.problem.count')
  return problems
}

/** A sentence for an RRULE nextime does not offer (EC-9): rhythm, days, end; never a raw rule. */
export function rruleSentence(rrule: string, first: string, t: TFunction, language: string): string {
  const parts = Object.fromEntries(rrule.split(';').map((part) => part.split('=') as [string, string]))
  const count = Math.max(1, Number(parts.INTERVAL ?? 1) || 1)
  const days = (parts.BYDAY ?? '').split(',').filter(Boolean)
  const named = days.map((entry) => {
    const found = /^([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)$/.exec(entry)
    if (!found) return entry
    const weekday = weekdayName(found[2] as Weekday, language)
    if (!found[1]) return weekday
    const number = Number(found[1])
    return t('rule.nthWeekday', { nth: t(number < 0 ? 'rule.ordinal.last' : `rule.ordinal.${Math.min(number, 5)}`), weekday })
  })
  const monthDays = (parts.BYMONTHDAY ?? '').split(',').filter(Boolean).map((day) => (Number(day) < 0 ? t('rule.ordinal.last') : dayOrdinal(Number(day), language)))
  let base: string
  if (parts.FREQ === 'DAILY') base = t('rule.daily', { count })
  else if (parts.FREQ === 'WEEKLY') base = t('rule.weekly', { count, days: named.length ? list(named, language) : weekdayName(weekdayOf(first), language) })
  else if (parts.FREQ === 'MONTHLY') base = t('rule.monthlyOn', { count, what: list(named.length ? named : monthDays.length ? monthDays : [dayOrdinal(Number(first.slice(8, 10)), language)], language) })
  else if (parts.FREQ === 'YEARLY') base = t('rule.yearlyPlain', { count })
  else base = t('rule.other')
  const out = [base]
  if (parts.UNTIL) out.push(t('rule.until', { date: shortDate(`${parts.UNTIL.slice(0, 4)}-${parts.UNTIL.slice(4, 6)}-${parts.UNTIL.slice(6, 8)}`, language) }))
  if (parts.COUNT) out.push(t('rule.count', { count: Number(parts.COUNT) }))
  return out.join(', ')
}
