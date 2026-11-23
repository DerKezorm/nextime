/**
 * The column beside Month, Week and Day while the window is wide enough (TIME-12, TIME-13, decided while testing,
 * 2026-11-23): what is shown now, one calendar alone (its name with its mark), a collection (its name) or "Alles"
 * ("Kommende Termine"), each with the number, as nexnotes' head of a list; "Demnächst" and then by month, a series once
 * with its next occurrence and its rule (the list one wants for birthdays); "Vergangen" folded at the end. Each date
 * sheet in the color of its event's calendar. Free rows as nexnotes' notes, the one open with the accent bar; more on
 * scrolling; new as the views hear of changes. Folded away by its button, kept per device. Without a connection what
 * is kept, as far as it goes.
 */
import { CalendarPlus, PanelRightClose, PanelRightOpen } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { useDevicePref } from '../../lib/devicePref'
import { useDisplay } from '../../lib/useDisplay'
import type { CalendarEntry, Collection } from '../calendars/api'
import { useCalendars, usePanel, useRevision } from '../calendars/store'
import { colorVar, Swatch } from '../calendars/Swatch'
import { clockText, wallOf } from '../calendars/time'
import type { Shown } from '../collections/store'
import { useOffline } from '../offline/connection'
import { useKept } from '../offline/snapshot'
import type { Task, TaskArea } from '../nextasks/api'
import { useTasks } from '../nextasks/hooks'
import { TaskBox, TaskTitle, Waiting } from '../nextasks/TaskItem'
import { DEADLINES_ROW, type Deadline } from '../nexpaper/api'
import { DeadlineMark, DeadlineTitle } from '../nexpaper/DeadlineItem'
import { useDeadlines } from '../nexpaper/hooks'
import { DateSheet } from './DateSheet'
import { addDays, daysBetween, format, shortRule } from './dates'

/** From this window width the column stands beside the month: a sidebar of up to 440 px, the month and the column
 * all fit (at 1530 px with a wide sidebar it stood over the edge). */
export const COLUMN_FROM = '(min-width: 1200px)'
const PAGE = 50
/** "Nächste Termine" beside Today: ten, then "Weitere anzeigen" (TIME-11). */
const NEXT_PAGE = 10
/** The most rows one request brings (the server's limit). */
const PAGE_MAX = 500
/** How far ahead the lists show tasks of nextasks (TIME-25 column). */
const TASK_DAYS = 90

export type ListedEvent = {
  id: number
  occurrence: string | null
  calendar_id: number
  title: string
  all_day: boolean
  start: string
  end: string
  time_zone: string
  recurring: boolean
  coming: boolean
  location: string
  busy_only: boolean
  rrule: string
  /** The first day of a series ("seit 1954"). */
  first?: string | null
  /** The member's invitation in it: dashed, "Einladung: …" while open (TIME-18 AC-1, AC-7). */
  invitation?: { state: string; by: string } | null
}
type Page = { total: number; future_total: number; items: ListedEvent[] }

/** What the column lists: the calendar shown alone, a collection, or every calendar shown. */
export type ColumnScope = { kind: 'alone'; calendar: CalendarEntry } | { kind: 'area'; area: TaskArea } | { kind: 'collection'; collection: Collection } | { kind: 'all' }

// eslint-disable-next-line react-refresh/only-export-components
export function scopeOf(shown: Shown): ColumnScope {
  if (shown.only) return { kind: 'alone', calendar: shown.only }
  if (shown.onlyArea) return { kind: 'area', area: shown.onlyArea }
  if (shown.collection) return { kind: 'collection', collection: shown.collection }
  return { kind: 'all' }
}

// eslint-disable-next-line react-refresh/only-export-components
export const scopeKeyOf = (shown: Shown) => scopeKey(scopeOf(shown))

const scopeKey = (scope: ColumnScope) =>
  scope.kind === 'alone' ? `alone-${scope.calendar.id}` : scope.kind === 'area' ? `area-${scope.area.id}` : scope.kind === 'collection' ? `collection-${scope.collection.id}-${scope.collection.calendar_ids.join(',')}` : 'all'

/** Whether the column is folded away, on this device; open from the start. */
const FOLDED = 'columnFolded'

// eslint-disable-next-line react-refresh/only-export-components
export function useColumnFolded(): [boolean, (folded: boolean) => void] {
  const [folded, store] = useDevicePref(FOLDED, (raw) => raw === '1')
  return [folded, (value) => store(value ? '1' : null)]
}

/** The column, or the button that brings it back at the right edge while it is folded away. */
export function ViewColumn({ shown, now }: { shown: Shown; now: number }) {
  const { t } = useTranslation()
  const [folded, setFolded] = useColumnFolded()
  const scope = scopeOf(shown)
  if (folded)
    return (
      <div className="flex shrink-0 items-start border-l border-ink-700 pt-1 pl-1">
        <button type="button" className="nt-btn nt-btn-ghost h-8 w-8 px-0" aria-label={t('views.column.unfold')} title={t('views.column.unfold')} onClick={() => setFolded(false)} data-testid="column-unfold">
          <PanelRightOpen className="h-4 w-4" aria-hidden />
        </button>
      </div>
    )
  return <CalendarColumn key={scopeKey(scope)} scope={scope} now={now} onFold={() => setFolded(true)} />
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWide(): boolean {
  return useSyncExternalStore(
    (listener) => {
      const found = typeof window.matchMedia === 'function' ? window.matchMedia(COLUMN_FROM) : null
      found?.addEventListener?.('change', listener)
      return () => found?.removeEventListener?.('change', listener)
    },
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(COLUMN_FROM).matches : false),
  )
}

/** `mode` "next": the list beside Today, "Nächste Termine" (TIME-11, asked for while testing, 2026-11-23): from
 * tomorrow on, only what still comes, without background calendars, ten at a time with "Weitere anzeigen". */
export function CalendarColumn({ scope, now, onFold, mode = 'column' }: { scope: ColumnScope; now: number; onFold?: () => void; mode?: 'column' | 'next' }) {
  const { t, i18n } = useTranslation()
  const { list } = useCalendars()
  const seen = useRevision()
  const calendar = scope.kind === 'alone' ? scope.calendar : null
  const { zone, twelve } = useDisplay()
  const language = i18n.language
  const { target, open } = usePanel()
  const offline = useOffline()
  const { snapshot } = useKept()
  const [page, setPage] = useState<Page | null>(null)
  const [past, setPast] = useState<ListedEvent[] | null>(null)
  const [pastOpen, setPastOpen] = useState(false)
  const [failed, setFailed] = useState(false)
  const loading = useRef(false)
  /** How many rows stand loaded: a refresh asks for as many again. */
  const shownCount = useRef(0)
  const pastCount = useRef(0)
  // The moment of the first page: later pages count from it, so nothing slips between them. A change heard anew
  // (the views' live news) starts again from now.
  const [asked, setAsked] = useState(() => new Date(now).toISOString())
  const first = useRef(seen)
  useEffect(() => {
    if (first.current === seen) return
    first.current = seen
    setAsked(new Date().toISOString())
  }, [seen])
  const calendarId = calendar?.id ?? 0
  const calendarIds = scope.kind === 'collection' ? scope.collection.calendar_ids.join(',') : ''
  const kind = scope.kind
  const next = mode === 'next'
  const tomorrow = addDays(wallOf(now, zone).date, 1)
  // Coming tasks of nextasks among the events (TIME-25 column, asked for while testing, 2026-11-23): what the views
  // show of them, a repeating one once with its next day.
  const { tasks: taskList } = useTasks(addDays(tomorrow, TASK_DAYS), { from: next ? tomorrow : addDays(tomorrow, -1) })
  // Coming deadlines of nexpaper among them, as far ahead as the tasks.
  const { deadlines: deadlineList } = useDeadlines(addDays(tomorrow, TASK_DAYS), { from: next ? tomorrow : addDays(tomorrow, -1) })

  const fetchPage = useCallback(
    (offset: number, limit = next ? NEXT_PAGE : PAGE) => {
      const query: Record<string, string> = { now: asked, zone, offset: String(offset), limit: String(limit) }
      if (next) Object.assign(query, { background: 'false', from: tomorrow })
      if (kind === 'alone') return api<Page>(`/api/search/calendar/${calendarId}`, { query })
      return api<Page>('/api/search/upcoming', { query: kind === 'collection' ? { ...query, calendars: calendarIds } : query })
    },
    // `seen`: every change heard asks anew, also within the same second.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [kind, calendarId, calendarIds, asked, zone, next, tomorrow, seen],
  )

  useEffect(() => {
    let live = true
    // An area of nextasks alone: its tasks only, no event to ask for.
    if (kind === 'area') {
      setPage({ total: 0, future_total: 0, items: [] })
      return
    }
    // What stood stays while the new state comes (no blink on every change); a change heard anew asks for as many
    // as were loaded and keeps "Vergangen" open with its rows (review of the additions, 2026-11-23).
    setFailed(false)
    const loaded = shownCount.current
    fetchPage(0, Math.min(Math.max(loaded, next ? NEXT_PAGE : PAGE), PAGE_MAX)).then(
      (found) => {
        if (!live) return
        if (!found || !Array.isArray(found.items)) return setFailed(true)
        setPage(found)
        const pastLoaded = pastCount.current
        if (pastLoaded > 0 && found.total > found.future_total)
          fetchPage(found.future_total, Math.min(pastLoaded, PAGE_MAX)).then(
            (older) => live && setPast(older.items.filter((item) => !item.coming)),
            () => undefined,
          )
        else setPast(null)
      },
      () => live && setFailed(true),
    )
    return () => {
      live = false
    }
  }, [fetchPage, next, kind])

  const coming = page ? page.items.filter((item) => item.coming) : []
  shownCount.current = page ? page.items.length : 0
  pastCount.current = pastOpen && past ? past.length : 0
  const more = async () => {
    if (!page || loading.current) return
    loading.current = true
    try {
      if (!pastOpen && page.items.length < page.future_total) {
        const next = await fetchPage(page.items.length)
        setPage({ ...page, items: [...page.items, ...next.items] })
      } else if (pastOpen && past && page.future_total + past.length < page.total) {
        const next = await fetchPage(page.future_total + past.length)
        setPast([...past, ...next.items.filter((item) => !item.coming)])
      }
    } catch {
      // The rows shown stay; the next scroll asks again.
    } finally {
      loading.current = false
    }
  }
  const showPast = async () => {
    const opening = !pastOpen
    setPastOpen(opening)
    if (opening && page && past === null) {
      try {
        setPast((await fetchPage(page.future_total)).items.filter((item) => !item.coming))
      } catch {
        setPast([])
      }
    }
  }

  // Without a connection and nothing from the server: what is kept of the calendars listed (the weeks kept, coming
  // only); hidden calendars never, unless the one shown alone.
  const within = (id: number) => {
    if (scope.kind === 'alone') return id === scope.calendar.id
    if (scope.kind === 'area') return false
    if (list?.calendars.find((entry) => entry.id === id)?.hidden) return false
    return scope.kind === 'all' || scope.collection.calendar_ids.includes(id)
  }
  const kept: ListedEvent[] =
    !page && (offline || failed) && snapshot
      ? snapshot.occurrences
          .filter((row) => within(row.calendar_id) && Date.parse(row.all_day ? `${row.end}T23:59:59Z` : row.end) >= now)
          .filter((row) => !next || (!list?.calendars.find((entry) => entry.id === row.calendar_id)?.background && (row.all_day ? row.start : wallOf(Date.parse(row.start), zone).date) >= tomorrow))
          .map((row) => ({ id: row.id, occurrence: row.occurrence, calendar_id: row.calendar_id, title: row.title, all_day: row.all_day, start: row.start, end: row.end, time_zone: row.time_zone ?? '', recurring: row.recurring, coming: true, location: row.location ?? '', busy_only: row.busy_only, rrule: '' }))
      : []
  const rows = page ? coming : kept
  // Tasks up to the last event loaded; all of them once every coming event stands.
  const allLoaded = !page || page.items.length >= page.future_total
  const lastLoaded = rows.length ? rows[rows.length - 1] : null
  const taskRows = (taskList ?? []).filter((task) => !task.preview)
  const tasksShown = taskRows.filter((task) => allLoaded || !lastLoaded || task.day <= (lastLoaded.all_day ? lastLoaded.start : wallOf(Date.parse(lastLoaded.start), zone).date))
  const deadlinesShown = (deadlineList ?? []).filter((deadline) => allLoaded || !lastLoaded || deadline.day <= (lastLoaded.all_day ? lastLoaded.start : wallOf(Date.parse(lastLoaded.start), zone).date))
  const total = (page ? page.total : kept.length) + taskRows.length + (deadlineList ?? []).length

  const today = wallOf(now, zone).date
  const dayOf = (item: ListedEvent) => (item.all_day ? item.start : wallOf(Date.parse(item.start), zone).date)
  const lastDayOf = (item: ListedEvent) => (item.all_day ? item.end : wallOf(Date.parse(item.end) - 1, zone).date)
  const chosen = (item: ListedEvent) => target?.kind === 'open' && target.id === item.id && (target.occurrence ?? null) === (item.occurrence ?? null)

  /** "heute", "morgen", "in 239 Tagen", "gestern", "vor 3 Tagen". */
  const distance = (date: string) => {
    const days = daysBetween(today, date)
    if (days === 0) return t('views.column.today')
    if (days === 1) return t('views.column.tomorrow')
    if (days === -1) return t('views.column.yesterday')
    return days > 0 ? t('views.column.inDays', { count: days }) : t('views.column.daysAgo', { count: -days })
  }
  /** The quiet line under the title after the distance: time, "bis 24. Juli", the rule in short ("jährlich seit 1954";
   * "seit" only for a first year before this one). */
  const subline = (item: ListedEvent) => {
    const day = dayOf(item)
    const parts: string[] = []
    if (!item.all_day) parts.push(clockText(wallOf(Date.parse(item.start), zone).time, language, twelve))
    const last = lastDayOf(item)
    if (last > day) parts.push(t('views.column.until', { day: format(last, language, { day: 'numeric', month: 'long' }) }))
    const rule = item.recurring && item.rrule ? shortRule(item.rrule, t) : ''
    if (rule) {
      const since = item.first && item.first.slice(0, 4) < today.slice(0, 4) ? ` ${t('views.column.since', { year: item.first.slice(0, 4) })}` : ''
      parts.push(rule + since)
    }
    return parts
  }

  const taskRow = (task: Task) => {
    const away = daysBetween(today, task.day)
    const rule = task.repeat ? shortRule(task.repeat, t) : ''
    return (
      <li key={`task-${task.id}`}>
        <div className="flex min-h-[50px] w-full items-center gap-2.5 border-l-2 border-transparent py-1.5 pr-2 pl-2 hover:bg-ink-800" data-task={task.id} data-testid="column-task">
          <DateSheet day={task.day} language={language} color="var(--color-mist-500)" />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex min-w-0 items-center gap-2 text-[13.5px] font-medium text-mist-100">
              <TaskBox task={task} small />
              <TaskTitle task={task} />
              <Waiting task={task} />
            </span>
            <span className="truncate text-xs text-mist-500" data-testid="column-sub">
              {t('tasks.kind')} ·{' '}
              <span className={away === 0 || away === 1 ? 'text-accent-400' : ''}>{distance(task.day)}</span>
              {task.time ? ` · ${clockText(task.time, language, twelve)}` : ''}
              {rule ? ` · ${rule}` : ''}
            </span>
          </span>
        </div>
      </li>
    )
  }

  const deadlineRow = (deadline: Deadline) => {
    const away = daysBetween(today, deadline.day)
    return (
      <li key={`deadline-${deadline.id}`}>
        <div className="flex min-h-[50px] w-full items-center gap-2.5 border-l-2 border-transparent py-1.5 pr-2 pl-2 hover:bg-ink-800" data-deadline={deadline.id} data-testid="column-deadline">
          <DateSheet day={deadline.day} language={language} color="var(--color-mist-500)" />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="flex min-w-0 items-center gap-2 text-[13.5px] font-medium text-mist-100">
              <DeadlineMark small />
              <DeadlineTitle deadline={deadline} />
            </span>
            <span className="truncate text-xs text-mist-500" data-testid="column-sub">
              {t('deadlines.kind')} · <span className={away === 0 || away === 1 ? 'text-accent-400' : ''}>{distance(deadline.day)}</span>
              {deadline.sender ? ` · ${deadline.sender}` : ''}
            </span>
          </span>
        </div>
      </li>
    )
  }

  const colorOf = (item: ListedEvent) => colorVar(calendar?.color ?? list?.calendars.find((entry) => entry.id === item.calendar_id)?.color ?? 'gray')
  const row = (item: ListedEvent) => {
    const day = dayOf(item)
    const away = daysBetween(today, day)
    const title = item.busy_only ? t('views.busy') : item.title || t('views.columnNoTitle')
    return (
      <li key={`${item.id}-${item.occurrence ?? ''}`}>
        <button
          type="button"
          className={'flex min-h-[50px] w-full items-center gap-2.5 border-l-2 py-1.5 pr-2 pl-2 text-left hover:bg-ink-800 ' + (chosen(item) ? 'border-accent-500 bg-[var(--color-accent-wash)]' : 'border-transparent')}
          onClick={() => open({ kind: 'open', id: item.id, occurrence: item.occurrence })}
          data-testid="column-row"
          data-chosen={chosen(item) || undefined}
        >
          <DateSheet day={day} language={language} color={colorOf(item)} />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="truncate text-[13.5px] font-medium text-mist-100" title={title} data-testid="column-title">
              {item.invitation?.state === 'open' || item.invitation?.state === 'tentative' ? (
                <span className="nt-invitation-text" data-testid="invitation-title">
                  {item.invitation.state === 'open' ? t('invitations.titleOpen', { title }) : title}
                </span>
              ) : (
                title
              )}
            </span>
            <span className="truncate text-xs text-mist-500" data-testid="column-sub">
              <span className={away === 0 || away === 1 ? 'text-accent-400' : ''} data-testid="column-distance">
                {distance(day)}
              </span>
              {subline(item).map((part) => ` · ${part}`)}
            </span>
          </span>
        </button>
      </li>
    )
  }

  const heading = calendar ? calendar.name : scope.kind === 'area' ? (scope.area.id === DEADLINES_ROW ? t('deadlines.mine') : scope.area.name) : scope.kind === 'collection' ? scope.collection.name : t('views.column.coming')

  // Events and tasks by day (a day's events first), then "Demnächst" for the next seven days and a group per month.
  type Entry = { day: string; event?: ListedEvent; task?: Task; deadline?: Deadline }
  const entries: Entry[] = rows.map((item) => ({ day: dayOf(item), event: item }))
  for (const task of tasksShown) {
    const at = entries.findIndex((entry) => entry.day > task.day)
    entries.splice(at === -1 ? entries.length : at, 0, { day: task.day, task })
  }
  for (const deadline of deadlinesShown) {
    const at = entries.findIndex((entry) => entry.day > deadline.day)
    entries.splice(at === -1 ? entries.length : at, 0, { day: deadline.day, deadline })
  }
  const groups: { key: string; label: string; items: Entry[] }[] = []
  for (const item of entries) {
    const key = daysBetween(today, item.day) < 7 ? 'soon' : item.day.slice(0, 7)
    const last = groups.at(-1)
    if (last && last.key === key) last.items.push(item)
    else groups.push({ key, label: key === 'soon' ? t('views.column.soon') : format(`${key}-01`, language, { month: 'long', year: 'numeric' }), items: [item] })
  }

  const listed = groups.map((group) => (
    <section key={group.key} aria-label={group.label}>
      <p className="sticky top-0 z-10 bg-ink-950 px-3 pt-2.5 pb-1 text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase" data-testid="column-month">
        {group.label}
      </p>
      <ul>{group.items.map((item) => (item.task ? taskRow(item.task) : item.deadline ? deadlineRow(item.deadline) : row(item.event!)))}</ul>
    </section>
  ))

  if (next)
    return (
      <section className="min-w-0" aria-labelledby="next-events" data-testid="next-events">
        <h2 id="next-events" className="px-1 pb-1 text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase">
          {t('views.nextEvents')}
        </h2>
        {!page && !failed && !offline && <div className="mx-1 h-16 animate-pulse rounded-[9px] bg-ink-800" aria-hidden />}
        {((page && page.future_total === 0) || (!page && (offline || failed) && !kept.length)) && !tasksShown.length && !deadlinesShown.length && (
          <div className="nt-empty py-6" data-testid="next-events-empty">
            <CalendarPlus className="h-6 w-6" strokeWidth={1.6} aria-hidden />
            <p>{t('views.column.none')}</p>
          </div>
        )}
        {listed}
        {page && page.items.length < page.future_total && (
          <button type="button" className="nt-btn nt-btn-quiet mt-1 text-sm" onClick={() => void more()} data-testid="next-events-more">
            {t('views.column.more')}
          </button>
        )}
      </section>
    )

  return (
    <aside
      className="nt-scroll flex w-[clamp(240px,22vw,300px)] shrink-0 flex-col overflow-y-auto border-l border-ink-700 pl-1"
      aria-label={heading}
      onScroll={(event) => {
        const box = event.currentTarget
        if (box.scrollTop + box.clientHeight >= box.scrollHeight - 120) void more()
      }}
      data-testid="calendar-column"
    >
      <div className="flex items-center gap-2 pt-1 pr-1 pb-2 pl-3">
        {calendar && <Swatch color={calendar.color} icon={calendar.icon} size={14} />}
        <h2 className="min-w-0 truncate text-[15px] font-semibold text-mist-100" title={heading} data-testid="column-heading">
          {heading}
        </h2>
        <span className="text-[13px] text-mist-500" data-testid="column-count">
          {page || kept.length ? total : ''}
        </span>
        {onFold && (
          <button type="button" className="nt-btn nt-btn-ghost ml-auto h-8 w-8 shrink-0 px-0" aria-label={t('views.column.fold')} title={t('views.column.fold')} onClick={onFold} data-testid="column-fold">
            <PanelRightClose className="h-4 w-4" aria-hidden />
          </button>
        )}
      </div>
      {!page && !failed && !offline && <div className="mx-3 h-16 animate-pulse rounded-[9px] bg-ink-800" aria-hidden />}
      {page && page.total === 0 && !tasksShown.length && !deadlinesShown.length && (
        <div className="nt-empty mx-2" data-testid="column-empty">
          <CalendarPlus className="h-6 w-6" strokeWidth={1.6} aria-hidden />
          <p>{calendar ? t('views.columnEmpty', { name: calendar.name }) : t('views.column.none')}</p>
          {calendar?.writable && !offline && (
            <button type="button" className="nt-btn nt-btn-ghost" onClick={() => open({ kind: 'new', calendarId: calendar.id })} data-testid="column-new">
              {t('nav.newEvent')}
            </button>
          )}
        </div>
      )}
      {listed}
      {page && page.total > page.future_total && (
        <div className="mt-2 border-t border-ink-700 pt-1">
          <button type="button" className="w-full px-3 py-2 text-left text-xs font-semibold text-mist-500 hover:text-mist-100" aria-expanded={pastOpen} onClick={() => void showPast()} data-testid="column-past">
            {t('views.columnPast', { count: page.total - page.future_total })} {pastOpen ? '▴' : '▾'}
          </button>
          {pastOpen && past && <ul data-testid="column-past-list">{past.map(row)}</ul>}
        </div>
      )}
    </aside>
  )
}
