/**
 * Reading from what is kept, without a connection (TIME-28): the occurrences of a window for the calendars shown
 * (AC-3), whether a window lies outside what is kept (EC-3), and the search in it (EC-11).
 */
import type { Occurrence } from '../calendars/api'
import { addDays, clockText, dayName, wallOf } from '../calendars/time'
import type { Found, Hit } from '../search/api'
import type { KeptOccurrence, Snapshot } from './snapshot'

/** Whether the window [start, stop) reaches outside what is kept: then the views say it shows with a connection only. */
export function outsideKept(snapshot: Snapshot, start: number, stop: number): boolean {
  return start < Date.parse(snapshot.start) || stop > Date.parse(snapshot.end)
}

function inCalendars(row: Occurrence, only: Set<number> | null): boolean {
  if (!only) return true
  if (only.has(row.calendar_id)) return true
  return !!row.sources?.some((source) => only.has(source.calendar_id))
}

/**
 * The kept occurrences in the window from day `first` to day `end` (exclusive; for whole days) and the instants
 * [start, stop) (for times), for `calendars` (`null`: all kept), as the server would have given them.
 */
export function keptWindow(snapshot: Snapshot, first: string, end: string, start: number, stop: number, calendars: number[] | null): Occurrence[] {
  const only = calendars ? new Set(calendars) : null
  return snapshot.occurrences.filter((row) => {
    if (!inCalendars(row, only)) return false
    if (row.all_day) return row.start < end && row.end >= first
    const from = Date.parse(row.start)
    const to = Date.parse(row.end)
    return from < stop && (to > start || (to === from && from >= start))
  })
}

/** One occurrence kept, for the side panel without a connection. */
export function keptOccurrence(snapshot: Snapshot, id: number, occurrence: string | null): KeptOccurrence | null {
  return snapshot.occurrences.find((row) => row.id === id && (row.occurrence ?? null) === (occurrence ?? null)) ?? snapshot.occurrences.find((row) => row.id === id) ?? null
}

/** A text as the server's search compares it (`ical.fold_text`: NFC, case folded); ß and final sigma as Python folds
 * them, so "Straße" finds "STRASSE" here as there. */
export function fold(text: string): string {
  return text.normalize('NFC').toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ').replaceAll('ſ', 's')
}

/**
 * The search without a connection (EC-11): every word of `q` in the title, place or notes of a kept occurrence (each
 * series once, its next occurrence), for `calendars`. "Busy only" carries no text and is never found. Coming first
 * (the next on top), then past (the latest on top), as the server answers.
 */
export function keptSearch(snapshot: Snapshot, q: string, calendars: number[] | null, now: number, today: string): Found {
  const words = q.split(/\s+/).filter(Boolean).map(fold)
  const only = calendars ? new Set(calendars) : null
  const coming = (row: Occurrence) => (row.all_day ? row.end >= today : Date.parse(row.end) > now)
  const hits = new Map<string, { row: KeptOccurrence; coming: boolean }>()
  for (const row of snapshot.occurrences) {
    if (row.busy_only || !inCalendars(row, only) || !words.length) continue
    const text = fold([row.title, row.location ?? '', row.notes ?? ''].join('\n'))
    if (!words.every((word) => text.includes(word))) continue
    const key = row.recurring ? `${row.id}` : `${row.id}-${row.occurrence ?? ''}`
    const known = hits.get(key)
    const isComing = coming(row)
    // A series once: its next occurrence from now, or its last one when none comes.
    if (!known || (isComing && (!known.coming || row.start < known.row.start)) || (!isComing && !known.coming && row.start > known.row.start)) {
      hits.set(key, { row, coming: isComing })
    }
  }
  const items: Hit[] = [...hits.values()].map(({ row, coming: isComing }) => ({
    id: row.id,
    occurrence: row.occurrence,
    calendar_id: row.calendar_id,
    title: row.title,
    recurring: row.recurring,
    declined: false,
    waiting: !!row.waiting,
    coming: isComing,
    all_day: row.all_day,
    start: row.start,
    end: row.end,
    time_zone: row.time_zone,
  }))
  const upcoming = items.filter((hit) => hit.coming).sort((a, b) => a.start.localeCompare(b.start))
  const past = items.filter((hit) => !hit.coming).sort((a, b) => b.start.localeCompare(a.start))
  return { total: items.length, future_total: upcoming.length, offset: 0, items: [...upcoming, ...past], behind: [] }
}

/** "heute 15:52", "gestern 18:10", "Mo, 23.11. 09:00": when the kept state was taken, on the clock shown. */
export function stateTime(taken: number, now: number, zone: string, language: string, twelve: boolean, words: { today: string; yesterday: string }): string {
  const at = wallOf(taken, zone)
  const today = wallOf(now, zone).date
  const time = clockText(at.time, language, twelve)
  if (at.date === today) return `${words.today} ${time}`
  if (at.date === addDays(today, -1)) return `${words.yesterday} ${time}`
  return `${dayName(at.date, language)} ${time}`
}
