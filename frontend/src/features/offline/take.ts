/**
 * Taking the snapshot for reading without a connection (TIME-28 AC-3): a week back and four weeks ahead in the zone
 * shown, the calendars, the collections and the own profile, kept for the account that asked.
 */
import { api, type Me } from '../../api/client'
import { currentAccount } from '../../state/account'
import { calendarsApi, collectionsApi } from '../calendars/api'
import { addDays, instantOf, wallOf } from '../calendars/time'
import { watchSlow } from './connection'
import { keep, type KeptOccurrence } from './snapshot'

/** Days kept before today (the week shown today begins before it) and after it (the four weeks of the spec). */
export const DAYS_BEFORE = 7
export const DAYS_AFTER = 28
/** At least this long between two snapshots after changes; a change waits a moment so that a burst is one. */
export const GAP_MS = 60_000
export const SETTLE_MS = 2_000

export const offlineApi = {
  snapshot: (start: number, end: number) =>
    api<{ taken_at: string; start: string; end: string; occurrences: KeptOccurrence[] }>('/api/offline', {
      query: { start: new Date(start).toISOString(), end: new Date(end).toISOString() },
    }),
}

/** The window kept from `now` in `zone`: from the start of the day a week back to the end of the day four weeks on. */
export function keptSpan(now: number, zone: string): { start: number; end: number } {
  const today = wallOf(now, zone).date
  return { start: instantOf(addDays(today, -DAYS_BEFORE), '00:00', zone), end: instantOf(addDays(today, DAYS_AFTER + 1), '00:00', zone) }
}

let lastTaken = 0

/** When the last snapshot was taken (the device's clock); 0 before the first. */
export function lastTakenAt(): number {
  return lastTaken
}
let taking: Promise<boolean> | null = null

/** Takes and keeps a snapshot now; whether it worked. */
export async function takeSnapshot(zone: string, me: Me): Promise<boolean> {
  if (taking) return taking
  taking = (async () => {
    const account = me.id
    const now = Date.now()
    const span = keptSpan(now, zone)
    try {
      const [taken, calendars, collections] = await watchSlow(Promise.all([offlineApi.snapshot(span.start, span.end), calendarsApi.list(), collectionsApi.list()]))
      if (currentAccount() !== account) return false
      lastTaken = Date.now()
      return await keep({
        account,
        takenAt: now,
        start: taken.start,
        end: taken.end,
        me,
        calendars,
        collections: Array.isArray(collections?.collections) ? collections.collections : [],
        occurrences: Array.isArray(taken.occurrences) ? taken.occurrences : [],
      })
    } catch {
      return false
    } finally {
      taking = null
    }
  })()
  return taking
}

/** For the tests: as after a fresh page load. */
export function forgetTaking(): void {
  lastTaken = 0
  taking = null
}

