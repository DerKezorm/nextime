/**
 * What the views share on the page: the day they show (switching Day, Week, Month keeps it, TIME-12 AC-1), the clock of
 * the device that moves on by itself (TIME-11 EC-3, EC-11), whether the page is as narrow as a phone, and the
 * occurrences of a window for the calendars shown (TIME-13 AC-3).
 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'

import { useDevicePref } from '../../lib/devicePref'
import { useDisplay } from '../../lib/useDisplay'
import { eventsApi, type Occurrence } from '../calendars/api'
import { useRevision } from '../calendars/store'
import { instantOf, wallOf } from '../calendars/time'
import { useShown } from '../collections/store'
import { onAccountChange } from '../../state/account'
import { useOffline, watchSlow } from '../offline/connection'
import { keptWindow, outsideKept } from '../offline/kept'
import { useKept } from '../offline/snapshot'

// ---- Where a calendar shown alone leads ------------------------------------------------------------------------------

/** The views of the calendar. Showing a calendar, an area or the deadlines alone keeps the view on screen (asked for
 * while testing, 2026-11-24); from anywhere else (settings, search, trash) it opens Month. */
export const VIEW_PATHS: readonly string[] = ['/', '/day', '/week', '/month', '/year', '/upcoming']

/** The page to go to after showing something alone from `path`, or `null` to stay. */
export function afterAlone(path: string): string | null {
  return VIEW_PATHS.includes(path) ? null : '/month'
}

// ---- The calendar in the sidebar -------------------------------------------------------------------------------

/** The views of the calendar itself, as the switch above names them; "Kalender" in the sidebar stands for all of them. */
export const CALENDAR_PATHS: readonly string[] = ['/day', '/week', '/month', '/year']
const LAST_VIEW = 'calendarView'
const parseView = (raw: string | null): string => (raw && CALENDAR_PATHS.includes(`/${raw}`) ? `/${raw}` : '/month')

/** Where "Kalender" leads: the view this device showed last (Day, Week, Month or Year), the month from the start; and
 * how a view notes itself. The span itself is chosen only above, in the switch (asked for while testing, 2026-11-24). */
export function useLastView(): [string, (kind: 'day' | 'week' | 'month' | 'year') => void] {
  const [path, store] = useDevicePref(LAST_VIEW, parseView)
  return [path, (kind) => store(kind)]
}

// ---- The day shown --------------------------------------------------------------------------------------------------

let shownDay: string | null = null
const dayListeners = new Set<() => void>()

function subscribeDay(listener: () => void): () => void {
  dayListeners.add(listener)
  return () => dayListeners.delete(listener)
}

export function setViewDay(day: string | null): void {
  shownDay = day
  for (const listener of [...dayListeners]) listener()
}

onAccountChange(() => setViewDay(null))

/** The day the views show; today until the member moves away. */
export function useViewDay(today: string): [string, (day: string) => void] {
  const day = useSyncExternalStore(subscribeDay, () => shownDay)
  return [day ?? today, setViewDay]
}

// ---- The clock --------------------------------------------------------------------------------------------------------

/** How often the clock moves on: "in 25 minutes" counts on, the day turns at midnight. */
export const TICK_MS = 30_000

/** Now on the clock of the device (never the server's, TIME-11 EC-11), moving on by itself. */
export function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS)
    const back = () => document.visibilityState === 'visible' && setNow(Date.now())
    document.addEventListener('visibilitychange', back)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', back)
    }
  }, [])
  return now
}

/** Today in the zone shown, at `now`. */
export function todayAt(now: number, zone: string): string {
  return wallOf(now, zone).date
}

// ---- Narrow like a phone ------------------------------------------------------------------------------------------

const PHONE = '(max-width: 699px)'

function query(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(PHONE) : null
}

/** Below 700 px the views take the phone's shape (the week as three days, the month with dots, TIME-12 EC-3). */
export function useNarrow(): boolean {
  return useSyncExternalStore(
    (listener) => {
      const found = query()
      found?.addEventListener?.('change', listener)
      return () => found?.removeEventListener?.('change', listener)
    },
    () => query()?.matches ?? false,
  )
}

// ---- Tablet upright and smaller ------------------------------------------------------------------------------------

/** Below 1024 px (a tablet upright, a phone) the frame has a head and the bar at the bottom instead of the sidebar, as
 * the mock-up has it (TIME-28 AC-5, EC-10); turning the tablet switches by itself. Tailwind's `lg` is the same edge. */
export const COMPACT = '(max-width: 1023px)'

function compactQuery(): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(COMPACT) : null
}

export function useCompact(): boolean {
  return useSyncExternalStore(
    (listener) => {
      const found = compactQuery()
      found?.addEventListener?.('change', listener)
      return () => found?.removeEventListener?.('change', listener)
    },
    () => compactQuery()?.matches ?? false,
  )
}

// ---- The occurrences of a window ------------------------------------------------------------------------------------

export type Window = {
  rows: Occurrence[] | null
  failed: boolean
  /** Without a connection, the window reaches past what is kept: it shows with a connection only (TIME-28 EC-3). */
  outside: boolean
  /** Asks again (the button "Try again"). */
  retry: () => void
}

/**
 * What each window showed last, so a view one comes back to (Week → Today → Week) stands at once instead of the
 * placeholder, and is then brought up to date. Small, and forgotten when the account changes.
 */
const lastShown = new Map<string, Occurrence[]>()
const LAST_SHOWN_MAX = 24
/** Forgets what the windows showed (another account; a test drawing anew). */
export function forgetShownWindows(): void {
  lastShown.clear()
}
onAccountChange(forgetShownWindows)

function remember(key: string, rows: Occurrence[]): void {
  lastShown.delete(key)
  lastShown.set(key, rows)
  while (lastShown.size > LAST_SHOWN_MAX) lastShown.delete(lastShown.keys().next().value as string)
}

/**
 * The occurrences from the start of day `first` to the start of day `end` (exclusive) in the zone shown, for the
 * calendars of the collection chosen (or the one shown alone). What came last stays while the next comes, so a
 * change from elsewhere does not flicker (TIME-11 EC-9).
 *
 * Without a connection (TIME-28 AC-3) the window comes from what the installed app kept; a server that takes longer
 * than `SLOW_MS` counts as gone (EC-4). A window reaching past what is kept says so (`outside`, EC-3).
 */
export function useWindow(first: string, end: string): Window {
  const { zone } = useDisplay()
  const shown = useShown()
  const seen = useRevision()
  const [again, setAgain] = useState(0)
  const [rows, setRows] = useState<{ key: string; rows: Occurrence[] } | null>(null)
  const [failed, setFailed] = useState(false)
  const calendars = shown.calendars === null ? null : shown.calendars.join(',')
  const key = `${first}|${end}|${zone}|${calendars}|${shown.hidden}`
  const pending = !!shown.pending
  const offline = useOffline()
  const { snapshot } = useKept()
  useEffect(() => {
    let live = true
    if (pending) return
    if (calendars === '') {
      setRows({ key, rows: [] })
      return
    }
    // Without a connection nothing is asked: what is kept answers.
    if (offline) return
    const start = new Date(instantOf(first, '00:00', zone))
    const stop = new Date(instantOf(end, '00:00', zone))
    // Another window, another collection, no connection: the question before is stopped, never left to hang on a slow
    // server (review of TIME-28, finding 2).
    const abort = typeof AbortController === 'function' ? new AbortController() : null
    watchSlow(eventsApi.window(start, stop, { calendars: calendars === null ? null : calendars.split(',').map(Number), hidden: shown.hidden }, abort?.signal))
      .then((answer) => {
        if (!live) return
        const got = Array.isArray(answer) ? answer : []
        remember(key, got)
        setRows({ key, rows: got })
        setFailed(false)
      })
      .catch((error) => {
        if (!live || (error instanceof DOMException && error.name === 'AbortError')) return
        setFailed(true)
      })
    return () => {
      live = false
      abort?.abort()
    }
  }, [key, first, end, zone, calendars, shown.hidden, seen, again, pending, offline])
  // Another window (the next week, another collection): nothing of the one before stands in it, only what this very
  // window showed last time.
  const current = rows && rows.key === key ? rows.rows : (lastShown.get(key) ?? null)
  // From what is kept: without a connection, and when asking failed with nothing shown for this window (a session
  // that ran out while offline stays readable until signed in again, EC-6; "Die letzten bleiben sichtbar").
  const fromKept = snapshot !== null && !pending && (offline || (failed && current === null))
  const kept = useMemo(() => {
    if (!fromKept || !snapshot) return null
    const start = instantOf(first, '00:00', zone)
    const stop = instantOf(end, '00:00', zone)
    const only = calendars === null ? null : calendars === '' ? [] : calendars.split(',').map(Number)
    return { rows: keptWindow(snapshot, first, end, start, stop, only), outside: outsideKept(snapshot, start, stop) }
  }, [fromKept, snapshot, first, end, zone, calendars])
  const retry = () => setAgain((value) => value + 1)
  if (kept) return { rows: kept.rows, failed: !offline && failed, outside: kept.outside, retry }
  // Without a connection and nothing kept: what this window showed last stays; without that, "not loaded".
  return { rows: current, failed: offline ? current === null : failed, outside: false, retry }
}

// ---- Commands from the keyboard ---------------------------------------------------------------------------------------

/** What a key asks of the view on screen: one step back or on (arrows), the jump to a calendar week (K), the choice of
 * a date (G). */
export type ViewCommand = 'previous' | 'next' | 'jump' | 'pick'
const commandListeners = new Set<(command: ViewCommand) => void>()

export function viewCommand(command: ViewCommand): boolean {
  for (const listener of [...commandListeners]) listener(command)
  return commandListeners.size > 0
}

/** The view on screen hears the keys while it stands there. */
export function useViewCommands(listener: (command: ViewCommand) => void): void {
  const latest = useRef(listener)
  useEffect(() => {
    latest.current = listener
  })
  useEffect(() => {
    const call = (command: ViewCommand) => latest.current(command)
    commandListeners.add(call)
    return () => {
      commandListeners.delete(call)
    }
  }, [])
}
