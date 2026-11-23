/**
 * Day and Week (TIME-12): the grid of a day's hours, 7 to 22 o'clock in view when it opens and the rest by scrolling
 * (AC-7), today marked with its column tinted and the line of now (AC-9), the weekend tinted in slate blue (AC-12),
 * whole days, long events and background entries in the row above (AC-8, EC-5). On a phone "Week" is three days from
 * the chosen one (EC-3).
 *
 * An event moves and stretches by dragging in steps of a quarter of an hour (AC-2): a series asks which of it (TIME-6),
 * one shown from several calendars in which (TIME-14 EC-2); read-only and "busy" stay where they are and say why
 * (EC-2). A click into free room opens a new event of an hour there, dragging over free room takes the time dragged
 * (AC-3); the event is made only when saved, once (EC-8). An invitation stands dashed with "Einladung · <Person>"
 * (TIME-18 AC-1, AC-7); "wartet" and "Stand 15:52" at events of connected sources (TIME-15 EC-2, EC-5).
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { errorText } from '../../lib/errors'
import { ApiError } from '../../api/client'
import { useTitle } from '../../lib/title'
import { useDisplay } from '../../lib/useDisplay'
import { eventsApi, type CalendarEntry, type Occurrence, type Scope, type Source } from '../calendars/api'
import { SeriesQuestion } from '../calendars/Recurrence'
import { SourceQuestion } from '../calendars/SourceQuestion'
import { colorVar, textColorVar } from '../calendars/Swatch'
import { announce, useCalendars, usePanel } from '../calendars/store'
import { clockText, dayName, wallOf } from '../calendars/time'
import { EmptyCollection } from '../collections/CollectionMenu'
import { useShown } from '../collections/store'
import { BackgroundLine, DayList, invited, LoadProblem, Marks, PersonMark, targetOf, Twice, useCalendarMap, useClock, useTitleOf } from './bits'
import { addDays, format, isoWeekOf, isWeekend, range, weekday, weekStartOf } from './dates'
import { bars, columns, hoursOf, isBackground, isHoliday, moved, multiDay, quarter, timedParts, touches, type Part } from './layout'
import { setViewDay, todayAt, useNarrow, useNow, useViewCommands, useViewDay, useWindow } from './state'
import { ViewHead, WeekJump } from './ViewHead'
import { useWide, ViewColumn } from './CalendarColumn'
import { useTasks } from '../nextasks/hooks'
import { TaskChip } from '../nextasks/TaskItem'
import { DeadlineChip } from '../nexpaper/DeadlineItem'
import { useDeadlines } from '../nexpaper/hooks'
import { OutsideKept } from '../offline/OfflineHint'
import { useOffline } from '../offline/connection'

/** Hours in view when the grid opens: 7 to 22 o'clock (AC-7). */
const FIRST_HOUR = 7
const HOURS_IN_VIEW = 15
const MIN_HOUR = 36

type Drag =
  | { kind: 'move' | 'stretch'; row: Occurrence; part: Part; column: number; x: number; y: number; moved: boolean; minutes: number; days: number }
  | { kind: 'new'; date: string; from: number; x: number; y: number; moved: boolean; to: number }

export function GridView({ kind }: { kind: 'day' | 'week' }) {
  const { t, i18n } = useTranslation()
  const { display, zone, twelve } = useDisplay()
  useTitle(kind === 'day' ? t('views.day') : t('nav.week'))
  const language = i18n.language
  const narrow = useNarrow()
  const now = useNow()
  const today = todayAt(now, zone)
  const [day, setDay] = useViewDay(today)
  const navigate = useNavigate()
  const [jumping, setJumping] = useState(false)
  const [picking, setPicking] = useState(false)
  const dates = useMemo(() => (kind === 'day' ? [day] : narrow ? range(day, 3) : range(weekStartOf(day, display.week_start), 7)), [kind, day, narrow, display.week_start])
  const loaded = useWindow(dates[0], addDays(dates[dates.length - 1], 1))
  const calendars = useCalendarMap()
  const shown = useShown()
  // Room enough: the events of what is shown in a column beside the grid, as beside the month (TIME-12, TIME-13).
  const wide = useWide()
  const column = !narrow && wide && !shown.empty && !shown.pending

  const step = (direction: -1 | 1) => setDay(addDays(day, direction * (kind === 'day' ? 1 : narrow ? 3 : 7)))
  useViewCommands((command) => {
    if (command === 'jump') setJumping(true)
    else if (command === 'pick') setPicking(true)
    else step(command === 'previous' ? -1 : 1)
  })

  // The week number of the days shown: the ISO week of their Thursday, whatever day the week begins (TIME-3 EC-13).
  const thursday = dates.find((date) => weekday(date) === 4) ?? dates[0]
  const week = display.week_numbers ? isoWeekOf(thursday).week : null
  const title =
    kind === 'day'
      ? format(day, language, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
      : new Intl.DateTimeFormat(language, narrow ? { day: 'numeric', month: 'short', timeZone: 'UTC' } : { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).formatRange(
          new Date(`${dates[0]}T00:00:00Z`),
          new Date(`${dates[dates.length - 1]}T00:00:00Z`),
        )

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 overflow-hidden">
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col gap-3 px-3 pt-3 sm:px-6 sm:pt-5" data-testid={kind === 'day' ? 'view-day' : 'view-week'}>
      <ViewHead kind={kind} title={title} week={week} narrow={narrow} onStep={step} onToday={() => setDay(today)} onJump={() => setJumping(true)} day={day} today={today} picking={picking} onPicking={setPicking} onPick={setDay} />
      {loaded.failed && <LoadProblem onRetry={loaded.retry} />}
      {loaded.outside && <OutsideKept />}
      {shown.empty ? (
        <EmptyCollection />
      ) : (
        <Grid dates={dates} rows={loaded.rows} today={today} now={now} calendars={calendars} language={language} twelve={twelve} narrow={narrow} onDay={(date) => (setViewDay(date), navigate('/day'))} />
      )}
      {jumping && (
        <WeekJump
          from={thursday}
          today={today}
          onClose={() => setJumping(false)}
          onJump={(monday) => {
            setJumping(false)
            setDay(monday)
            if (kind === 'day') navigate('/week')
          }}
          onDate={(date) => {
            setJumping(false)
            setDay(date)
          }}
        />
      )}
    </div>
    {column && <ViewColumn shown={shown} now={now} />}
    </div>
  )
}

function Grid({ dates, rows, today, now, calendars, language, twelve, narrow, onDay }: {
  dates: string[]
  rows: Occurrence[] | null
  today: string
  now: number
  calendars: Map<number, CalendarEntry>
  language: string
  twelve: boolean
  narrow: boolean
  onDay: (date: string) => void
}) {
  const { t } = useTranslation()
  const { zone } = useDisplay()
  const { list } = useCalendars()
  const { open } = usePanel()
  const titleOf = useTitleOf()
  const scroller = useRef<HTMLDivElement>(null)
  const body = useRef<HTMLDivElement>(null)
  const [hour, setHour] = useState(MIN_HOUR)
  const [drag, setDrag] = useState<Drag | null>(null)
  const dragRef = useRef<Drag | null>(null)
  const [asking, setAsking] = useState<null | { kind: 'source'; row: Occurrence; then: (source: Source) => void } | { kind: 'series'; question: string; date: string; then: (scope: Scope) => void }>(null)
  const [list_, setList] = useState<{ date: string; rows: Occurrence[] } | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [held, setHeld] = useState<{ key: string; date: string; from: number; to: number } | null>(null)

  // 7 to 22 in view (AC-7): the height of an hour from the room there is; then scrolled to 7 o'clock.
  useLayoutEffect(() => {
    const height = scroller.current?.clientHeight ?? 0
    const next = Math.max(narrow ? 44 : MIN_HOUR, Math.floor(height / HOURS_IN_VIEW))
    setHour(next)
    if (scroller.current) scroller.current.scrollTop = FIRST_HOUR * next
  }, [narrow, dates.length])

  useEffect(() => {
    if (!note) return
    const timer = window.setTimeout(() => setNote(null), 3200)
    return () => window.clearTimeout(timer)
  }, [note])
  // What was dropped stays at its new place until the new state arrives.
  useEffect(() => setHeld(null), [rows])

  const parts = useMemo(() => timedParts((rows ?? []).filter((row) => !isBackground(row, calendars) && !row.all_day && !multiDay(row, zone)), dates, zone), [rows, dates, zone, calendars])
  const top = (rows ?? []).filter((row) => !isBackground(row, calendars) && (row.all_day || multiDay(row, zone)))
  const laid = bars(top, dates, zone)
  const lanes = dates.length === 1 ? 8 : 3
  const nowWall = wallOf(now, zone)
  const offline = useOffline()

  const keyOf = (row: Occurrence) => `${row.id}-${row.occurrence ?? ''}`
  const fixed = (row: Occurrence) => row.busy_only || (row.sources ? !row.sources.some((source) => source.writable) : row.writable === false)

  const columnAt = (x: number): number => {
    const rect = body.current?.getBoundingClientRect()
    const width = rect && rect.width > 0 ? (rect.width - (narrow ? 34 : 44)) / dates.length : 100
    return Math.floor((x - (rect?.left ?? 0) - (narrow ? 34 : 44)) / width)
  }

  const save = (row: Occurrence, minutes: number, days: number, stretch: boolean) => {
    const times = moved(row, zone, minutes, days, stretch)
    const send = (source: Source | null, scope: Scope) => {
      setAsking(null)
      const id = source?.id ?? row.id
      const occurrence = source ? source.occurrence : row.occurrence
      const etag = source?.etag ?? row.etag
      eventsApi
        .change(id, { all_day: false, ...times, scope, occurrence, etag: etag ?? '' })
        .then(() => announce())
        .catch((error) => {
          setHeld(null)
          setNote(error instanceof ApiError ? errorText(error.code, error.values) : t('panel.offline'))
        })
    }
    const withScope = (source: Source | null) => {
      const occurrence = source ? source.occurrence : row.occurrence
      if (!row.recurring || !occurrence) return send(source, 'all')
      const date = times.start.slice(0, 10)
      const time = clockText(times.start.slice(11, 16), language, twelve)
      const when = date === wallOf(Date.parse(row.start), row.time_zone || zone).date ? time : `${dayName(date, language)} ${time}`
      setAsking({ kind: 'series', question: t('series.move', { title: row.title, when }), date: wallOf(Date.parse(row.start), zone).date, then: (scope) => send(source, scope) })
    }
    if (row.sources && row.sources.length > 1) setAsking({ kind: 'source', row, then: (source) => withScope(source) })
    else withScope(null)
  }

  const down = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !rows) return
    const target = event.target as HTMLElement
    const block = target.closest<HTMLElement>('[data-event]')
    const column = target.closest<HTMLElement>('[data-date]')
    if (!column || (!block && (target.closest('button') || target.closest('[data-task]')))) return
    const index = dates.indexOf(column.dataset.date!)
    if (block) {
      const found = parts.get(column.dataset.date!)?.find((part) => keyOf(part.row) === block.dataset.event)
      if (!found) return
      dragRef.current = { kind: target.dataset.edge ? 'stretch' : 'move', row: found.row, part: found, column: index, x: event.clientX, y: event.clientY, moved: false, minutes: 0, days: 0 }
    } else {
      // Without a connection nothing new from the grid (TIME-28 AC-3).
      if (offline) return
      const rect = column.getBoundingClientRect()
      const from = Math.max(0, Math.min(23.75, Math.floor(((event.clientY - rect.top) / hour) * 4) / 4))
      dragRef.current = { kind: 'new', date: column.dataset.date!, from, to: from + 1, x: event.clientX, y: event.clientY, moved: false }
    }
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      // Not every pointer can be captured (a test, an old browser); the grid still hears it.
    }
  }

  const move = (event: ReactPointerEvent<HTMLDivElement>) => {
    const current = dragRef.current
    if (!current) return
    const dx = event.clientX - current.x
    const dy = event.clientY - current.y
    if (!current.moved && Math.hypot(dx, dy) < 5) return
    if (!current.moved && current.kind !== 'new' && fixed(current.row)) {
      dragRef.current = null
      setNote(t('views.cannotMove', { title: titleOf(current.row, false) }))
      return
    }
    let next: Drag
    if (current.kind === 'new') next = { ...current, moved: true, to: Math.max(current.from + 0.25, current.from + quarter(dy / hour)) }
    else {
      const minutes = Math.round(quarter(dy / hour) * 60)
      const days = current.kind === 'move' ? Math.max(-current.column, Math.min(dates.length - 1 - current.column, columnAt(event.clientX) - current.column || 0)) : 0
      next = { ...current, moved: true, minutes, days: Number.isFinite(days) ? days : 0 }
    }
    dragRef.current = next
    setDrag(next)
  }

  const up = () => {
    const current = dragRef.current
    dragRef.current = null
    setDrag(null)
    if (!current) return
    if (current.kind === 'new') {
      if (offline) return
      const start = timeOf(current.from)
      // Only a time dragged over more than one step is the member's; a click leaves the length to the calendar's
      // defaults (review of the additions, 2026-11-23).
      if (!current.moved || current.to - current.from <= 0.25) return open({ kind: 'new', start: `${current.date}T${start.time}` })
      const end = timeOf(current.to)
      open({ kind: 'new', start: `${current.date}T${start.time}`, end: `${addDays(current.date, end.days)}T${end.time}` })
      return
    }
    if (!current.moved) return open(targetOf(current.row))
    if (current.minutes === 0 && current.days === 0) return
    // Without a connection nothing moves; it says why (TIME-28 AC-3).
    if (offline) return setNote(t('offline.moveOff'))
    const from = current.kind === 'stretch' ? current.part.from : current.part.from + current.minutes / 60
    const to = current.kind === 'stretch' ? Math.max(current.part.from + 0.25, current.part.to + current.minutes / 60) : current.part.to + current.minutes / 60
    setHeld({ key: keyOf(current.row), date: dates[current.column + current.days], from, to })
    save(current.row, current.minutes, current.days, current.kind === 'stretch')
  }

  const rowsOfDay = (date: string) => (rows ?? []).filter((row) => !isBackground(row, calendars) && touches(row, date, zone))
  // Tasks of nextasks on their day (TIME-25 AC-2): without a time in the all-day row, with one at its place.
  const { tasks } = useTasks(dates[dates.length - 1], { from: dates[0], previews: true })
  const tasksOn = (date: string) => (tasks ?? []).filter((task) => task.day === date)
  // Deadlines of nexpaper in the all-day row of their day to act by.
  const { deadlines } = useDeadlines(dates[dates.length - 1], { from: dates[0] })
  const deadlinesOn = (date: string) => (deadlines ?? []).filter((deadline) => deadline.day === date)
  const hourLabel = (value: number) => (twelve ? `${value % 12 || 12} ${value < 12 ? 'AM' : 'PM'}` : narrow ? String(value) : `${value}:00`)
  const sources = list?.calendars ?? []

  const holidays = new Set(dates.filter((date) => isHoliday((rows ?? []).filter((row) => isBackground(row, calendars) && touches(row, date, zone)), calendars)))
  const tint = (date: string) => (isWeekend(date) ? 'nt-weekend ' : '') + (holidays.has(date) ? 'nt-holiday ' : '')
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="grid">
      <div className="nt-grid-head nt-gutter text-xs" style={{ '--n': dates.length } as CSSProperties}>
        <span />
        {dates.map((date) => (
          <button
            key={date}
            type="button"
            className={'flex items-baseline gap-1.5 border-l border-line px-1.5 py-1.5 text-left ' + tint(date)}
            aria-label={t('views.dayAria', { day: format(date, language, { weekday: 'long', day: 'numeric', month: 'long' }) })}
            aria-current={date === today ? 'date' : undefined}
            onClick={() => onDay(date)}
            data-testid="week-day"
          >
            <div className="text-mist-500">{format(date, language, { weekday: 'short' })}</div>
            <div className={date === today ? 'rounded-[10px] bg-accent-500 px-1.5 font-bold text-on-accent' : 'font-semibold text-mist-100'}>{Number(date.slice(8))}</div>
          </button>
        ))}
      </div>
      <div
        className={'nt-all-day nt-gutter text-xs ' + (offline || narrow ? '' : 'nt-all-day-addable')}
        style={{ '--n': dates.length } as CSSProperties}
        onClick={(event) => {
          // A click into the free space of the all-day row makes a new all-day event on that day (TIME-12 AC-3).
          const target = event.target as HTMLElement
          // Not on a phone, as the month (TIME-12, decided while testing): a tap there is for scrolling.
          if (offline || narrow || target.closest('button') || target.closest('[data-task]') || target.closest('[data-deadline]')) return
          const cells = [...event.currentTarget.querySelectorAll<HTMLElement>('[data-all-day]')]
          const cell = target.closest<HTMLElement>('[data-all-day]') ?? cells.find((one) => {
            const box = one.getBoundingClientRect()
            return event.clientX >= box.left && event.clientX < box.right
          })
          const date = cell?.dataset.allDay
          if (date) open({ kind: 'new', start: date, end: date, allDay: true })
        }}
        data-testid="all-day-row"
      >
        <span className="pt-1 text-[10.5px] text-mist-500">{t('views.allDayShort')}</span>
        {dates.map((date, index) => {
          const background = (rows ?? []).filter((row) => isBackground(row, calendars) && touches(row, date, zone))
          return (
            <div key={date} className={'min-w-0 border-l border-line px-1 pt-0.5 ' + tint(date) + (date === today ? 'nt-today-column' : '')} style={{ gridColumn: index + 2, gridRow: 1 }} data-all-day={date} data-testid="all-day-cell">
              <BackgroundLine rows={background} calendars={calendars} max={dates.length === 1 ? 4 : 1} />
              {tasksOn(date)
                .filter((task) => !task.time)
                .map((task) => (
                  <TaskChip key={task.id} task={task} />
                ))}
              {deadlinesOn(date).map((deadline) => (
                <DeadlineChip key={deadline.id} deadline={deadline} />
              ))}
            </div>
          )
        })}
        {laid
          .filter((bar) => bar.lane < lanes)
          .map((bar) => {
            const calendar = calendars.get(bar.row.calendar_id)
            return (
              <button
                key={keyOf(bar.row)}
                type="button"
                className={'nt-bar ' + (bar.row.busy_only ? 'nt-busy ' : '') + (bar.cutStart ? 'nt-cut-start ' : '') + (bar.cutEnd ? 'nt-cut-end' : '')}
                style={{ gridColumn: `${bar.first + 2} / ${bar.last + 3}`, gridRow: bar.lane + 2, '--k': colorVar(calendar?.color ?? 'gray'), '--kt': textColorVar(calendar?.color ?? 'gray') } as CSSProperties}
                title={titleOf(bar.row)}
                onClick={() => open(targetOf(bar.row))}
                data-testid="all-day-bar"
              >
                {titleOf(bar.row)}
              </button>
            )
          })}
        {dates.map((date, index) => {
          const hidden = laid.filter((bar) => bar.lane >= lanes && bar.first <= index && index <= bar.last).length
          if (!hidden) return null
          return (
            <button key={`more-${date}`} type="button" className="nt-btn-quiet px-1 text-left text-[11px] font-semibold" style={{ gridColumn: index + 2, gridRow: lanes + 2 }} onClick={() => setList({ date, rows: rowsOfDay(date) })} data-testid="all-day-more">
              +{hidden}
            </button>
          )
        })}
      </div>
      <div ref={scroller} className="nt-scroll relative min-h-0 flex-1 overflow-y-auto border-t border-ink-700 [scrollbar-gutter:stable]" data-testid="grid-scroll">
        {rows === null ? (
          <div className="space-y-3 p-4" aria-hidden data-testid="grid-loading">
            <span className="nt-skeleton h-3 w-1/2" />
            <span className="nt-skeleton h-3 w-2/3" />
          </div>
        ) : null}
        <div
          ref={body}
          className="nt-grid-body"
          style={{ '--n': dates.length, '--h': `${hour}px` } as CSSProperties}
          data-hour={hour}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={() => ((dragRef.current = null), setDrag(null))}
          data-testid="grid-body"
        >
          <div className="relative">
            {Array.from({ length: 23 }, (_, index) => index + 1).map((value) => (
              <span key={value} className="absolute right-1.5 -translate-y-[7px] text-[11px] text-mist-500" style={{ top: `calc(var(--h) * ${value})` }} data-testid="hour-label">
                {hourLabel(value)}
              </span>
            ))}
          </div>
          {dates.map((date) => {
            const placed = columns(parts.get(date) ?? [])
            return (
              <div key={date} className={'nt-column ' + tint(date) + (date === today ? 'nt-today-column' : '')} data-date={date} data-testid="grid-column">
                {placed.placed.map(({ part, column, columns: count }) => {
                  const key = keyOf(part.row)
                  if (held?.key === key) return null
                  const dragging = drag && drag.kind !== 'new' && keyOf(drag.row) === key && drag.moved
                  return <Block key={`${key}-${date}`} part={part} column={column} count={count} faded={!!dragging} fixed={fixed(part.row)} />
                })}
                {placed.more.map((more) => (
                  <button key={`more-${more.from}`} type="button" className="nt-more" style={{ top: `calc(var(--h) * ${more.from})` }} onClick={() => setList({ date, rows: more.parts.map((part) => part.row) })} aria-label={t('views.moreEvents', { count: more.parts.length })} data-testid="grid-more">
                    +{more.parts.length}
                  </button>
                ))}
                {held && held.date === date && <Ghost from={held.from} to={held.to} />}
                {drag && drag.moved && drag.kind !== 'new' && dates[drag.column + drag.days] === date && (
                  <Ghost from={drag.kind === 'stretch' ? drag.part.from : drag.part.from + drag.minutes / 60} to={drag.kind === 'stretch' ? Math.max(drag.part.from + 0.25, drag.part.to + drag.minutes / 60) : drag.part.to + drag.minutes / 60} />
                )}
                {drag && drag.kind === 'new' && drag.moved && drag.date === date && <Ghost from={drag.from} to={drag.to} />}
                {tasksOn(date)
                  .filter((task) => task.time)
                  .map((task) => (
                    <TaskChip key={task.id} task={task} at={hoursOf(task.time!)} />
                  ))}
                {date === today && <div className="nt-now" style={{ '--s': hoursOf(nowWall.time) } as CSSProperties} aria-hidden data-testid="now-line" />}
              </div>
            )
          })}
        </div>
      </div>
      {note && (
        <p role="status" className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-[9px] bg-ink-700 px-4 py-2 text-sm text-mist-100 shadow-xl md:bottom-6" data-testid="grid-note">
          {note}
        </p>
      )}
      {list_ && <DayList date={list_.date} rows={list_.rows} onClose={() => setList(null)} onDay={() => (setList(null), onDay(list_.date))} />}
      {asking?.kind === 'source' && (
        <SourceQuestion title={asking.row.title} sources={asking.row.sources ?? []} calendars={sources} onChoose={asking.then} onClose={() => (setAsking(null), setHeld(null))} />
      )}
      {asking?.kind === 'series' && (
        <SeriesQuestion question={asking.question} day={dayName(asking.date, language)} single onChoose={asking.then} onClose={() => (setAsking(null), setHeld(null))} />
      )}
    </div>
  )
}

function timeOf(hours: number): { time: string; days: number } {
  const minutes = Math.round(hours * 60)
  const within = ((minutes % 1440) + 1440) % 1440
  return { time: `${String(Math.floor(within / 60)).padStart(2, '0')}:${String(within % 60).padStart(2, '0')}`, days: Math.floor(minutes / 1440) }
}

function Ghost({ from, to }: { from: number; to: number }) {
  return <div className="nt-ev nt-ev-ghost" style={{ '--s': from, '--d': Math.max(0.25, to - from), '--l': 0, '--w': 1 } as CSSProperties} aria-hidden data-testid="drag-ghost" />
}

function Block({ part, column, count, faded, fixed }: { part: Part; column: number; count: number; faded: boolean; fixed: boolean }) {
  const { t } = useTranslation()
  const { list } = useCalendars()
  const { open } = usePanel()
  const titleOf = useTitleOf()
  const clock = useClock()
  const row = part.row
  const calendar = list?.calendars.find((entry) => entry.id === row.calendar_id)
  const color = calendar?.color ?? 'gray'
  const key = `${row.id}-${row.occurrence ?? ''}`
  const invitation = invited(row)
  const classes = ['nt-ev', row.busy_only ? 'nt-busy' : '', invitation ? 'nt-invitation' : '', fixed ? 'nt-read-only' : '', part.before ? 'nt-continues-before' : '', part.after ? 'nt-continues-after' : '', faded ? 'opacity-40' : ''].join(' ')
  return (
    <div
      role="button"
      tabIndex={0}
      className={classes}
      style={{ '--s': part.from, '--d': Math.max(0.25, part.to - part.from), '--l': column / count, '--w': 1 / count, '--k': colorVar(color), '--kt': textColorVar(color) } as CSSProperties}
      aria-label={`${titleOf(row)}, ${clock(Date.parse(row.start))}–${clock(Date.parse(row.end))}${fixed ? `, ${t('views.readOnly')}` : ''}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          open(targetOf(row))
        }
      }}
      data-event={key}
      data-testid="grid-event"
    >
      <span className="nt-ev-title">{row.busy_only ? t('views.busy') : row.title}</span>
      <span className="opacity-85" data-testid="grid-event-sub">
        {invitation ? t('invitations.by', { name: row.invitation!.by }) : row.busy_only && row.person ? row.person.name : clock(Date.parse(row.start))}
      </span>
      <Twice row={row} />
      <span className="flex flex-wrap gap-1">
        <Marks row={row} calendar={calendar} />
      </span>
      {row.person && (
        <span className="nt-ev-person">
          <PersonMark person={row.person} size={18} />
        </span>
      )}
      {!fixed && <span className="nt-edge" data-edge="1" aria-hidden />}
    </div>
  )
}
