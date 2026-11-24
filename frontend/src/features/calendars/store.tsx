/* eslint-disable react-refresh/only-export-components */
/**
 * What the calendar features share on the page: the list of calendars (asked once, asked again after every change),
 * a counter that tells the views to ask for their events again, and which event the side panel shows.
 */
import { createContext, useCallback, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'

import { calendarsApi, type CalendarList, type Rule, type Source } from './api'
import { isOffline, noteFailure } from '../offline/connection'
import { keptNow } from '../offline/snapshot'
import { onAccountChange } from '../../state/account'

let revision = 0
const listeners = new Set<() => void>()

/** Something changed (a calendar, an event): every list and view asks again. */
export function announce(): void {
  revision += 1
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useRevision(): number {
  return useSyncExternalStore(subscribe, () => revision)
}

// ---- The list of calendars, one for the whole page ---------------------------------------------------------------
//
// Every part of the page (sidebar, views, column, panel) reads the same list, asked once per change: before, each
// asked on its own, six questions at a time, and the answers came at different moments, so the parts of a page
// switched one by one (the views first showed everything, then only the calendar shown alone).

type CalendarsState = { list: CalendarList | null; failed: boolean }
let calendarsState: CalendarsState = { list: null, failed: false }
/** The revision the list was last asked for and answered for; an older answer never replaces a newer one. */
let askedAt = -1
let answeredAt = -1
/** Grows when the account changes: answers for the account before are dropped. */
let generation = 0
const calendarListeners = new Set<() => void>()

function setCalendars(next: CalendarsState): void {
  calendarsState = next
  for (const listener of [...calendarListeners]) listener()
}

function subscribeCalendars(listener: () => void): () => void {
  calendarListeners.add(listener)
  return () => calendarListeners.delete(listener)
}

function askCalendars(at: number): void {
  if (askedAt >= at) return
  askedAt = at
  const mine = generation
  calendarsApi
    .list()
    .then((answer) => {
      if (mine !== generation || at < answeredAt) return
      answeredAt = at
      if (!answer || !Array.isArray(answer.calendars)) return setCalendars({ ...calendarsState, failed: true })
      setCalendars({ list: answer, failed: false })
    })
    .catch((error) => {
      if (mine !== generation || at < answeredAt) return
      answeredAt = at
      noteFailure(error)
      const kept = isOffline() ? keptNow() : null
      if (kept) setCalendars({ list: calendarsState.list ?? kept.calendars, failed: false })
      else setCalendars({ ...calendarsState, failed: true })
    })
}

/** Forgets the list (another account; a test drawing anew). */
export function forgetCalendars(): void {
  generation += 1
  askedAt = -1
  answeredAt = -1
  setCalendars({ list: null, failed: false })
}
onAccountChange(forgetCalendars)

/** The account's calendars; `null` while they come. Without a connection, those the installed app kept (TIME-28). */
export function useCalendars(): { list: CalendarList | null; failed: boolean; take: (list: CalendarList) => void } {
  const seen = useRevision()
  const state = useSyncExternalStore(subscribeCalendars, () => calendarsState)
  useEffect(() => askCalendars(seen), [seen])
  const take = useCallback((answer: CalendarList) => {
    setCalendars({ list: answer, failed: false })
    announce()
  }, [])
  const list = state.list ?? (isOffline() ? (keptNow()?.calendars ?? null) : null)
  return { list, failed: state.failed, take }
}

/**
 * Changes from elsewhere (a phone through the calendar account, another person) show at once (TIME-20 AC-4): the
 * page listens to `/api/live` and asks again when it hears "changed". While that stream is down (a proxy that cuts
 * it, a browser without it), the page asks every few seconds instead, and once more when it comes back to the front.
 */
export function LiveRefresh({ every = 15_000 }: { every?: number }) {
  useEffect(() => {
    let timer: number | null = null
    const poll = () => {
      if (timer === null) timer = window.setInterval(() => document.visibilityState === 'visible' && announce(), every)
    }
    const stopPolling = () => {
      if (timer !== null) window.clearInterval(timer)
      timer = null
    }
    let source: EventSource | null = null
    if (typeof EventSource === 'undefined') poll()
    else {
      source = new EventSource('/api/live')
      source.addEventListener('changed', () => announce())
      source.onopen = () => {
        stopPolling()
        // What changed while the stream was down.
        announce()
      }
      source.onerror = () => poll()
    }
    const back = () => {
      if (document.visibilityState === 'visible') announce()
    }
    document.addEventListener('visibilitychange', back)
    return () => {
      source?.close()
      stopPolling()
      document.removeEventListener('visibilitychange', back)
    }
  }, [every])
  return null
}

// ---- The side panel ---------------------------------------------------------------------------------------------

export type PanelTarget =
  /** A new event; `start` and `end` as `YYYY-MM-DDTHH:MM` (a click or a drag in the grid), or as dates for a whole day;
   * `title`, `calendarId` and `rule` from the quick field ("Mehr …", TIME-10 AC-2). */
  | { kind: 'new'; start?: string; end?: string; allDay?: boolean; title?: string; calendarId?: number; rule?: Rule | null }
  /** An event; `sources` when it stands in several calendars and is shown once (TIME-14 AC-3); `reveal` when it opens
   * from an address (a notification, a link from nexmail): the views behind then show its calendar. */
  | { kind: 'open'; id: number; occurrence: string | null; sources?: Source[]; reveal?: boolean }

type Panel = {
  target: PanelTarget | null
  open: (target: PanelTarget) => void
  close: () => void
  /** Set by the panel while it holds unsaved changes: whoever opens another asks first (TIME-5 AC-14). */
  dirty: boolean
  setDirty: (dirty: boolean) => void
}

const PanelContext = createContext<Panel | null>(null)

export function PanelProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<PanelTarget | null>(null)
  const [dirty, setDirty] = useState(false)
  const open = useCallback((next: PanelTarget) => setTarget(next), [])
  const close = useCallback(() => {
    setTarget(null)
    setDirty(false)
  }, [])
  return <PanelContext.Provider value={{ target, open, close, dirty, setDirty }}>{children}</PanelContext.Provider>
}

export function usePanel(): Panel {
  const value = useContext(PanelContext)
  if (!value) return { target: null, open: () => undefined, close: () => undefined, dirty: false, setDirty: () => undefined }
  return value
}
