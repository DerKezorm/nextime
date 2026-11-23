/* eslint-disable react-refresh/only-export-components */
/**
 * Collections (TIME-13) as the page holds them: the account's own collections from the server (the same on every
 * device, AC-10), which one is chosen and whether one calendar is shown alone ("Nur diesen zeigen", AC-11), both only
 * on this device (AC-3, EC-17). "All" is not stored anywhere: it is every calendar shown (AC-1, AC-5, EC-3).
 *
 * `useShown` says what every view asks for: the calendars of the choice (or all), and whether hidden ones count too.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react'

import { forgetOnSignOut, useDevicePref } from '../../lib/devicePref'
import { collectionsApi, type CalendarEntry, type CalendarList, type Collection } from '../calendars/api'
import { useCalendars, useRevision } from '../calendars/store'
import { DEADLINES_ROW, nexpaperStatus, onNexpaper } from '../nexpaper/api'
import { nextasksStatus, onNextasks, taskAreas, type TaskArea } from '../nextasks/api'
import { isOffline, noteFailure } from '../offline/connection'
import { keptNow } from '../offline/snapshot'

/** The stored choice of this device: "all" or the id of a collection. */
const CHOSEN = 'collection'
/** The calendar shown alone on this device; it lasts until signing out (EC-17). */
const ONLY = 'only'
forgetOnSignOut(ONLY)
/** The area of nextasks shown alone on this device, as a calendar alone (TIME-25 area rows). */
const ONLY_AREA = 'onlyArea'
forgetOnSignOut(ONLY_AREA)

type Collections = {
  list: Collection[] | null
  failed: boolean
  take: (list: Collection[]) => void
}

const Context = createContext<Collections | null>(null)

export function CollectionsProvider({ children }: { children: ReactNode }) {
  const seen = useRevision()
  // Without a connection, the collections the installed app kept (TIME-28).
  const [list, setList] = useState<Collection[] | null>(() => (isOffline() ? (keptNow()?.collections ?? null) : null))
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    collectionsApi
      .list()
      .then((answer) => {
        if (!live) return
        if (!answer || !Array.isArray(answer.collections)) return setFailed(true)
        setList(answer.collections)
        setFailed(false)
      })
      .catch((error) => {
        if (!live) return
        noteFailure(error)
        const kept = isOffline() ? keptNow() : null
        if (kept) setList((before) => before ?? kept.collections)
        else setFailed(true)
      })
    return () => {
      live = false
    }
  }, [seen])
  const take = useCallback((next: Collection[]) => setList(next), [])
  const value = useMemo(() => ({ list, failed, take }), [list, failed, take])
  return <Context.Provider value={value}>{children}</Context.Provider>
}

export function useCollections(): Collections {
  return useContext(Context) ?? { list: [], failed: false, take: () => undefined }
}

const parseChosen = (raw: string | null): number | 'all' => (raw && /^\d+$/.test(raw) ? Number(raw) : 'all')
const parseOnly = (raw: string | null): number | null => (raw && /^\d+$/.test(raw) ? Number(raw) : null)
const parseArea = (raw: string | null): string | null => (raw && /^[A-Za-z0-9-]{1,100}$/.test(raw) ? raw : null)

/** The collection chosen on this device; a stored choice that is gone counts as "All" (EC-2, EC-9). */
export function useChosen(): { chosen: Collection | null; pending: boolean; choose: (id: number | 'all') => void } {
  const { list, failed } = useCollections()
  const [stored, store] = useDevicePref(CHOSEN, parseChosen)
  const chosen = stored === 'all' ? null : (list?.find((entry) => entry.id === stored) ?? null)
  // A collection is chosen, but the collections have not come yet: the views wait instead of showing everything.
  const pending = stored !== 'all' && list === null && !failed
  return { chosen, pending, choose: (id) => store(id === 'all' ? null : String(id)) }
}

/** The calendar shown alone on this device, and how to start, end or change that (AC-11, AC-12). */
export function useOnly(): { only: number | null; setOnly: (id: number | null) => void; onlyArea: string | null; setOnlyArea: (id: string | null) => void } {
  const [only, store] = useDevicePref(ONLY, parseOnly)
  const [onlyArea, storeArea] = useDevicePref(ONLY_AREA, parseArea)
  return {
    only,
    onlyArea,
    setOnly: (id) => {
      storeArea(null)
      store(id === null ? null : String(id))
    },
    setOnlyArea: (id) => {
      store(null)
      storeArea(id)
    },
  }
}

export type Shown = {
  /** `null`: every calendar shown ("All"); otherwise only these. */
  calendars: number[] | null
  /** Hidden calendars count too (the one calendar shown alone may be a hidden one). */
  hidden: boolean
  /** The calendar shown alone, while it is. */
  only: CalendarEntry | null
  /** The chosen collection, `null` for "All". */
  collection: Collection | null
  /** A collection without a calendar to show (EC-1). */
  empty: boolean
  /** What to show is not known yet (the collections are still coming). */
  pending?: boolean
  /** The area of nextasks shown alone, while it is: no calendar then, its tasks only. */
  onlyArea?: TaskArea | null
}

export function shownOf(list: CalendarList | null, chosen: Collection | null, onlyId: number | null, area: TaskArea | null = null): Shown {
  const only = onlyId !== null ? (list?.calendars.find((entry) => entry.id === onlyId) ?? null) : null
  if (only) return { calendars: [only.id], hidden: true, only, collection: chosen, empty: false }
  if (area) return { calendars: [], hidden: false, only: null, collection: chosen, empty: false, onlyArea: area }
  if (!chosen) return { calendars: null, hidden: false, only: null, collection: null, empty: false }
  const known = new Set(list?.calendars.map((entry) => entry.id) ?? chosen.calendar_ids)
  const calendars = chosen.calendar_ids.filter((id) => known.has(id))
  const visible = calendars.filter((id) => !list?.calendars.find((entry) => entry.id === id)?.hidden)
  return { calendars, hidden: false, only: null, collection: chosen, empty: visible.length === 0 }
}

/** What the views show now. A calendar shown alone that is gone (deleted, no longer shared) ends that (EC-19). */
export function useShown(): Shown {
  const { list } = useCalendars()
  const { chosen, pending } = useChosen()
  const { only, setOnly, onlyArea, setOnlyArea } = useOnly()
  const areas = useSyncExternalStore(onNextasks, taskAreas)
  const status = useSyncExternalStore(onNextasks, nextasksStatus)
  const paper = useSyncExternalStore(onNexpaper, nexpaperStatus)
  const gone = only !== null && list !== null && !list.calendars.some((entry) => entry.id === only)
  // An area alone that nextasks no longer names (or no nextasks) ends that; while the areas come, its id stands.
  // "Meine Fristen" of nexpaper alone, as an area: ends when nexpaper is no longer connected.
  const deadlinesAlone = onlyArea === DEADLINES_ROW ? (paper?.connected === false ? null : { id: DEADLINES_ROW, name: '', icon: '' }) : undefined
  const area = onlyArea === null ? null : deadlinesAlone !== undefined ? deadlinesAlone : (areas.find((entry) => entry.id === onlyArea) ?? (areas.length || status?.connected === false ? null : { id: onlyArea, name: '', icon: '' }))
  const areaGone = onlyArea !== null && area === null
  useEffect(() => {
    if (gone) setOnly(null)
    if (areaGone) setOnlyArea(null)
  }, [gone, setOnly, areaGone, setOnlyArea])
  return { ...shownOf(list, chosen, gone ? null : only, only === null || gone ? area : null), pending: pending && only === null && area === null }
}

/**
 * The calendar a new event lands in (AC-4, AC-13, EC-18): the one shown alone when one may write into it; else the
 * default when it is in the collection, else the first writable calendar of the collection, else the default.
 * `readOnlyAlone` says that the calendar shown alone may only be read.
 */
export function suggestion(list: CalendarList | null, shown: Shown): { id: number | null; readOnlyAlone: string | null } {
  if (!list) return { id: null, readOnlyAlone: null }
  if (shown.only?.writable) return { id: shown.only.id, readOnlyAlone: null }
  const readOnlyAlone = shown.only ? shown.only.name : null
  const inside = shown.collection ? new Set(shown.collection.calendar_ids) : null
  const usable = (entry: CalendarEntry) => entry.writable && !entry.hidden && (!inside || inside.has(entry.id))
  const fallback = list.calendars.find((entry) => entry.id === list.default_id)
  if (fallback && usable(fallback)) return { id: fallback.id, readOnlyAlone }
  const first = list.calendars.find(usable)
  return { id: first?.id ?? list.default_id, readOnlyAlone }
}
