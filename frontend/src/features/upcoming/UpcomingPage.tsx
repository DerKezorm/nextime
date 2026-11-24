/**
 * "Demnächst" (asked for while testing, 2026-11-24, the concept "Wochen" chosen): not only what comes, but how full it
 * gets and where there is room.
 *
 * On top the situation: "Als Nächstes" as a card in the accent (what runs or comes next, how soon, when and where, and
 * what follows today), and the next 14 days as a strip of bars in the steps of the year's heat map, with the next
 * calm stretch named below it ("Ruhe: Fr 27. bis So 29. frei"); a bar leads to its day below. Then the weeks as columns
 * side by side: this week from today, the next one, the one after, and "Später", four weeks more with whole days only
 * (holidays, birthdays, things over several days), so a look far ahead does not drown in the everyday. Each week lists
 * its days with short rows (time, color, title) and the free days between ("Fr 27 – So 29 · 3 Tage frei"); a long week
 * shows its first rows and "+ n weitere". Four columns side by side on a large screen, two on a laptop, one on a phone.
 *
 * The chips in the head set the same choice as "Sammlung" and "Nur diesen", so the list and the calendar never
 * disagree. Without a connection what the installed app kept.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { useTitle } from '../../lib/title'
import { useDisplay } from '../../lib/useDisplay'
import type { CalendarEntry, Occurrence } from '../calendars/api'
import { useCalendars } from '../calendars/store'
import { EmptyCollection } from '../collections/CollectionMenu'
import { useChosen, useCollections, useOnly, useShown } from '../collections/store'
import { OutsideKept } from '../offline/OfflineHint'
import { Dot, ListTitle, LoadProblem, useCalendarMap, useClock, useOpen, useSpan } from '../views/bits'
import { addDays, daysBetween, format, isoWeekOf } from '../views/dates'
import { daysOf, isBackground, isHoliday, multiDay } from '../views/layout'
import { setViewDay, todayAt, useNow, useWindow } from '../views/state'
import { heatStep } from '../views/year'
import { calmStretch, columnsFrom, freeRuns, STRIP_DAYS, type Column } from './plan'

/** Rows a week shows before "+ n weitere". */
const WEEK_ROWS = 8

type Day = { date: string; events: Occurrence[]; background: Occurrence[] }

export function UpcomingPage() {
  const { t } = useTranslation()
  const { zone, display } = useDisplay()
  useTitle(t('nav.upcoming'))
  const now = useNow()
  const today = todayAt(now, zone)
  const columns = useMemo(() => columnsFrom(today, display.week_start), [today, display.week_start])
  const end = addDays(columns[columns.length - 1].last, 1)
  const loaded = useWindow(today, end)
  const calendars = useCalendarMap()
  const shown = useShown()
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [goTo, setGoTo] = useState<string | null>(null)

  // Each occurrence on its first day (today while it still runs); background entries apart; and how many events
  // touch each day (an event over several days on each of them, as the year counts).
  const { days, counts } = useMemo(() => {
    const found = new Map<string, Day>()
    const counted = new Map<string, number>()
    for (const row of loaded.rows ?? []) {
      const span = daysOf(row, zone)
      if (span.last < today) continue
      const date = span.first < today ? today : span.first
      if (date >= end) continue
      const day = found.get(date) ?? { date, events: [], background: [] }
      const quiet = isBackground(row, calendars)
      if (quiet) day.background.push(row)
      else {
        day.events.push(row)
        for (let touched = date; touched <= span.last && touched < end; touched = addDays(touched, 1)) counted.set(touched, (counted.get(touched) ?? 0) + 1)
      }
      found.set(date, day)
    }
    const order = (row: Occurrence) => (row.all_day ? '' : row.start)
    for (const day of found.values()) day.events.sort((a, b) => order(a).localeCompare(order(b)))
    return { days: found, counts: counted }
  }, [loaded.rows, zone, today, end, calendars])

  // A bar of the strip leads to its day: the column it is in opens, then the day comes into view.
  useEffect(() => {
    if (!goTo) return
    document.querySelector(`[data-testid="upcoming-day"][data-date="${goTo}"]`)?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
    setGoTo(null)
  }, [goTo, expanded])
  const showDay = (date: string) => {
    const column = columns.find((entry) => entry.first <= date && date <= entry.last)
    if (column) setExpanded((before) => new Set(before).add(column.first))
    setGoTo(date)
  }

  return (
    <div className="h-full min-h-0 w-full overflow-y-auto px-3 pt-3 pb-6 sm:px-6 sm:pt-5" data-testid="view-upcoming">
      <div className="@container flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-lg font-bold tracking-tight text-mist-100 sm:text-xl">{t('nav.upcoming')}</h1>
          <UpcomingFilter />
        </div>
        {loaded.failed && <LoadProblem onRetry={loaded.retry} />}
        {loaded.outside && <OutsideKept />}
        {shown.empty && <EmptyCollection />}
        {loaded.rows === null && !loaded.failed ? (
          <div className="grid gap-3 @[48rem]:grid-cols-2" aria-busy>
            {[0, 1].map((index) => (
              <span key={index} className="nt-skeleton h-28 w-full" />
            ))}
          </div>
        ) : (
          <>
            <div className="grid gap-3 @[48rem]:grid-cols-[minmax(0,1.1fr)_minmax(0,1.4fr)]">
              <NextCard rows={loaded.rows ?? []} now={now} today={today} calendars={calendars} />
              <Strip today={today} counts={counts} onDay={showDay} />
            </div>
            <div className="grid items-start gap-3 @[40rem]:grid-cols-2 @[72rem]:grid-cols-4" data-testid="upcoming-weeks">
              {columns.map((column) => (
                <WeekColumn
                  key={column.first}
                  column={column}
                  days={days}
                  counts={counts}
                  today={today}
                  now={now}
                  calendars={calendars}
                  open={expanded.has(column.first)}
                  onOpen={() => setExpanded((before) => new Set(before).add(column.first))}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function NextCard({ rows, now, today, calendars }: { rows: Occurrence[]; now: number; today: string; calendars: Map<number, CalendarEntry> }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const { zone } = useDisplay()
  const clock = useClock()
  const span = useSpan()
  const open = useOpen()
  // What runs or comes next with a time; whole days and long events are not "next" (as in Heute).
  const timed = rows.filter((row) => !isBackground(row, calendars) && !row.all_day && !multiDay(row, zone) && Date.parse(row.end) > now)
  const next = timed[0]
  if (!next) {
    return (
      <div className="rounded-[14px] border border-ink-700 bg-ink-850 px-4 py-3.5 text-sm text-mist-500" data-testid="upcoming-next">
        {t('upcoming.nothingNext')}
      </div>
    )
  }
  const start = Date.parse(next.start)
  const day = daysOf(next, zone).first
  const label =
    start <= now
      ? t('upcoming.running', { span: span(Math.max(1, Math.round((Date.parse(next.end) - now) / 60_000))) })
      : day === today
        ? t('upcoming.nextIn', { span: span(Math.max(1, Math.round((start - now) / 60_000))) })
        : t('upcoming.nextOn', { when: `${day === addDays(today, 1) ? t('views.column.tomorrow') : format(day, language, { weekday: 'short', day: 'numeric', month: 'short' })}, ${clock(start)}` })
  const after = timed.find((row) => row !== next && daysOf(row, zone).first === day && Date.parse(row.start) >= Date.parse(next.end))
  const place = [calendars.get(next.calendar_id)?.name, next.location].filter(Boolean).join(' · ')
  return (
    <button type="button" className="nt-next-card text-left" onClick={() => open(next)} data-testid="upcoming-next">
      <span className="text-xs font-semibold text-accent-400">{label}</span>
      <span className="nt-wrap text-xl font-bold text-mist-100">
        <ListTitle row={next} />
      </span>
      <span className="text-sm text-mist-300">
        {clock(start)}–{clock(Date.parse(next.end))}
        {place ? ` · ${place}` : ''}
      </span>
      {after && <span className="pt-1 text-xs text-mist-400">{t('upcoming.afterToday', { title: after.busy_only ? t('views.busy') : after.title, time: clock(Date.parse(after.start)) })}</span>}
    </button>
  )
}

function Strip({ today, counts, onDay }: { today: string; counts: Map<string, number>; onDay: (date: string) => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const strip = Array.from({ length: STRIP_DAYS }, (_, index) => {
    const date = addDays(today, index)
    return { date, count: counts.get(date) ?? 0 }
  })
  const calm = calmStretch(strip)
  const short = (date: string) => format(date, language, { weekday: 'short', day: 'numeric' }).replace('.,', '')
  return (
    <div className="rounded-[14px] border border-ink-700 bg-ink-850 px-4 py-3" data-testid="upcoming-strip">
      <div className="mb-2 text-xs text-mist-500">{t('upcoming.strip', { days: STRIP_DAYS })}</div>
      <div className="grid h-14 grid-cols-14 items-end gap-1">
        {strip.map((day) => (
          <button
            key={day.date}
            type="button"
            className="nt-strip-bar"
            data-step={heatStep(day.count)}
            style={{ height: `${day.count ? Math.max(18, (Math.min(day.count, 6) / 6) * 100) : 8}%` }}
            aria-label={`${format(day.date, language, { weekday: 'long', day: 'numeric', month: 'long' })}, ${day.count ? t('views.yearCount', { count: day.count }) : t('views.free')}`}
            title={`${format(day.date, language, { weekday: 'short', day: 'numeric', month: 'short' })} · ${day.count ? t('views.yearCount', { count: day.count }) : t('views.free')}`}
            onClick={() => onDay(day.date)}
            data-date={day.date}
            data-testid="upcoming-bar"
          />
        ))}
      </div>
      <div className="mt-1 grid grid-cols-14 gap-1 text-center text-[10px] text-mist-500" aria-hidden>
        {strip.map((day) => (
          <span key={day.date} className={day.date === today ? 'font-semibold text-accent-400' : ''}>
            {format(day.date, language, { weekday: 'short' }).replace('.', '').slice(0, 2)}
          </span>
        ))}
      </div>
      <div className={'mt-2 text-xs ' + (calm ? 'text-ok-500' : 'text-mist-500')} data-testid="upcoming-calm">
        {calm ? (calm.from === calm.to ? t('upcoming.calmOne', { day: short(calm.from) }) : t('upcoming.calm', { from: short(calm.from), to: short(calm.to) })) : t('upcoming.calmNone', { days: STRIP_DAYS })}
      </div>
    </div>
  )
}

function WeekColumn({ column, days, counts, today, now, calendars, open, onOpen }: { column: Column; days: Map<string, Day>; counts: Map<string, number>; today: string; now: number; calendars: Map<number, CalendarEntry>; open: boolean; onOpen: () => void }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const { zone } = useDisplay()
  const clock = useClock()
  const openEvent = useOpen()
  const navigate = useNavigate()
  const later = column.kind === 'later'
  const inside = [...days.values()].filter((day) => day.date >= column.first && day.date <= column.last).sort((a, b) => a.date.localeCompare(b.date))
  const all = inside.reduce((sum, day) => sum + day.events.length, 0)
  // "Später": whole days, things over several days and the background (holidays, birthdays) only.
  const shownDays = later
    ? inside.map((day) => ({ ...day, events: day.events.filter((row) => row.all_day || multiDay(row, zone)) })).filter((day) => day.events.length > 0 || day.background.length > 0)
    : inside
  const left = later ? all - shownDays.reduce((sum, day) => sum + day.events.length, 0) : 0
  const week = isoWeekOf(column.first).week
  const range = `${format(column.first, language, { day: 'numeric', month: 'short' })}–${format(column.last, language, { day: 'numeric', month: 'short' })}`
  const title = column.kind === 'this' ? t('upcoming.thisWeek') : column.kind === 'next' ? t('upcoming.nextWeek') : column.kind === 'week' ? t('views.weekNumber', { number: week }) : t('upcoming.later')
  const sub =
    column.kind === 'later'
      ? t('upcoming.laterSub', { range })
      : column.kind === 'week'
        ? t('upcoming.rangeSub', { range, count: all })
        : t('upcoming.weekSub', { number: week, count: all })
  const short = (date: string) => format(date, language, { weekday: 'short', day: 'numeric' }).replace('.,', '')
  const gap = (from: string, to: string, key: string) =>
    freeRuns(from, to, (date) => counts.get(date) ?? 0).map((free) => (
      <div key={`${key}-${free.first}`} className="nt-free-gap" data-testid="upcoming-gap">
        {free.count === 1 ? t('upcoming.freeOne', { day: short(free.first) }) : t('upcoming.freeMany', { from: short(free.first), to: short(free.last), days: free.count })}
      </div>
    ))

  const parts: ReactNode[] = []
  let rows = 0
  let hidden = 0
  let before = addDays(column.first, -1)
  for (const day of shownDays) {
    if (!open && rows >= WEEK_ROWS) {
      hidden += day.events.length
      continue
    }
    if (!later) parts.push(gap(before, day.date, `gap-${day.date}`))
    before = day.date
    const holiday = isHoliday(day.background, calendars)
    const away = daysBetween(today, day.date)
    const room = open || later ? day.events.length : Math.max(0, WEEK_ROWS - rows)
    const events = day.events.slice(0, room)
    hidden += day.events.length - events.length
    rows += events.length
    parts.push(
      <div key={day.date} className="pt-2" data-date={day.date} data-testid="upcoming-day">
        <div className="flex min-w-0 items-baseline gap-1.5 text-[13px]">
          <span className={'font-semibold ' + (holiday ? 'nt-holiday-name' : 'text-mist-100')}>{short(day.date)}</span>
          {away <= 1 && <span className="text-xs text-accent-400">{away === 0 ? t('views.column.today') : t('views.column.tomorrow')}</span>}
          {Number(day.date.slice(8)) === 1 && away > 1 && <span className="text-xs text-mist-500">{format(day.date, language, { month: 'long' })}</span>}
          {day.background.length > 0 && (
            <span className={'min-w-0 truncate text-xs ' + (holiday ? 'nt-holiday-name' : 'text-mist-500')} data-testid="upcoming-background">
              {day.background.map((row) => row.title).join(' · ')}
            </span>
          )}
        </div>
        {events.map((row) => {
          const span = daysOf(row, zone)
          const past = !row.all_day && Date.parse(row.end) <= now
          const until = span.last > day.date ? ` ${t('upcoming.until', { day: format(span.last, language, { day: 'numeric', month: 'short' }) })}` : ''
          return (
            <button
              key={`${row.id}-${row.occurrence}`}
              type="button"
              className={'flex w-full min-w-0 items-center gap-2 rounded-[7px] px-1 py-[3px] text-left text-[13px] hover:bg-ink-800 ' + (past ? 'opacity-50' : '')}
              title={`${row.busy_only ? t('views.busy') : row.title}${until}${calendars.get(row.calendar_id) ? ` · ${calendars.get(row.calendar_id)!.name}` : ''}`}
              onClick={() => openEvent(row)}
              data-testid="upcoming-event"
            >
              <span className="w-10 shrink-0 text-xs text-mist-500 tabular-nums">{row.all_day || span.first < day.date ? t('views.allDayShort') : clock(Date.parse(row.start))}</span>
              <Dot color={row.busy_only ? 'gray' : (calendars.get(row.calendar_id)?.color ?? 'gray')} size={7} />
              <span className="min-w-0 truncate text-mist-100">
                <ListTitle row={row} />
                {until && <span className="text-mist-500">{until}</span>}
              </span>
            </button>
          )
        })}
      </div>,
    )
  }
  if (!later && (open || hidden === 0)) parts.push(gap(before, addDays(column.last, 1), 'gap-end'))

  return (
    <section className="min-w-0 rounded-[14px] border border-ink-700 bg-ink-850 px-3.5 pt-3 pb-3.5" aria-label={title} data-testid="upcoming-week" data-kind={column.kind}>
      <div className="text-[15px] font-semibold text-mist-100">{title}</div>
      <div className="text-xs text-mist-500">{sub}</div>
      {shownDays.length === 0 ? <div className="pt-3 text-[13px] text-mist-500">{later ? t('upcoming.laterNothing') : t('upcoming.weekFree')}</div> : parts}
      {hidden > 0 && (
        <button type="button" className="mt-2 text-xs font-semibold text-accent-400" onClick={onOpen} data-testid="upcoming-more">
          {t('upcoming.more', { count: hidden })}
        </button>
      )}
      {later && left > 0 && (
        <button
          type="button"
          className="mt-2 block text-left text-xs text-accent-400"
          onClick={() => {
            setViewDay(column.first)
            navigate('/month')
          }}
          data-testid="upcoming-later-more"
        >
          {t('upcoming.laterMore', { count: left })}
        </button>
      )}
    </section>
  )
}

/**
 * "Alles", each collection and each calendar as chips: the same choice as "Sammlung" and "Nur diesen" in the sidebar
 * (one choice for the list and the calendar). A calendar chosen again ends showing it alone.
 */
function UpcomingFilter() {
  const { t } = useTranslation()
  const { list } = useCalendars()
  const { list: collections } = useCollections()
  const { chosen, choose } = useChosen()
  const { only, onlyArea, setOnly } = useOnly()
  const all = only === null && onlyArea === null && chosen === null
  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label={t('upcoming.filter')} data-testid="upcoming-filter">
      <button
        type="button"
        className="nt-chip"
        aria-pressed={all}
        onClick={() => {
          setOnly(null)
          choose('all')
        }}
        data-testid="upcoming-all"
      >
        {t('collections.all')}
      </button>
      {(collections ?? []).map((collection) => (
        <button
          key={`c-${collection.id}`}
          type="button"
          className="nt-chip"
          aria-pressed={only === null && onlyArea === null && chosen?.id === collection.id}
          onClick={() => {
            setOnly(null)
            choose(collection.id)
          }}
          data-testid="upcoming-collection"
        >
          {collection.name}
        </button>
      ))}
      {(list?.calendars ?? []).map((calendar) => (
        <button
          key={`k-${calendar.id}`}
          type="button"
          className="nt-chip"
          aria-pressed={only === calendar.id}
          onClick={() => setOnly(only === calendar.id ? null : calendar.id)}
          data-testid="upcoming-calendar"
        >
          <Dot color={calendar.color} size={8} />
          {calendar.name}
        </button>
      ))}
    </div>
  )
}
