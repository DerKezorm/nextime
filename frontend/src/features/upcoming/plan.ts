/**
 * The plan of "Demnächst" (asked for while testing, 2026-11-24): which days each column covers, and where the next calm
 * stretch lies. Pure, so the page and the tests share it.
 */
import { addDays, weekStartOf } from '../views/dates'

/** How far "Später" reaches after the three weeks, and how many days the strip on top shows. */
export const LATER_DAYS = 28
export const STRIP_DAYS = 14

export type Column = { kind: 'this' | 'next' | 'week' | 'later'; first: string; last: string }

/** This week from today, the next one, the one after, and "Später": four weeks more, whole days only. */
export function columnsFrom(today: string, weekStart: number): Column[] {
  const start = weekStartOf(today, weekStart)
  const columns: Column[] = [
    { kind: 'this', first: today, last: addDays(start, 6) },
    { kind: 'next', first: addDays(start, 7), last: addDays(start, 13) },
    { kind: 'week', first: addDays(start, 14), last: addDays(start, 20) },
  ]
  columns.push({ kind: 'later', first: addDays(start, 21), last: addDays(start, 20 + LATER_DAYS) })
  return columns
}

/** The first stretch of free days among `days` (in order), the longest from that point on that is free: a run of
 * at least two, else a single free day; `null` when every day holds something. */
export function calmStretch(days: { date: string; count: number }[]): { from: string; to: string } | null {
  let single: string | null = null
  for (let index = 0; index < days.length; index++) {
    if (days[index].count > 0) continue
    let end = index
    while (end + 1 < days.length && days[end + 1].count === 0) end++
    if (end > index) return { from: days[index].date, to: days[end].date }
    single ??= days[index].date
    index = end
  }
  return single ? { from: single, to: single } : null
}

/** The runs of free days between two shown days, `from` and `to` exclusive: days on which nothing runs (`busy` says
 * how many events touch a day; an event over several days stands only on its first, but keeps the others busy). */
export function freeRuns(from: string, to: string, busy: (date: string) => number): { first: string; last: string; count: number }[] {
  const runs: { first: string; last: string; count: number }[] = []
  for (let date = addDays(from, 1); date < to; date = addDays(date, 1)) {
    if (busy(date) > 0) continue
    const open = runs[runs.length - 1]
    if (open && addDays(open.last, 1) === date) {
      open.last = date
      open.count += 1
    } else runs.push({ first: date, last: date, count: 1 })
  }
  return runs
}
