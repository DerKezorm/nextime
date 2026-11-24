/**
 * The year as a heat map (TIME-12 AC-16 to AC-22, the same as TIMEIOS-8 in the app): the twelve months of the year of
 * the day shown, each day a field, the weeks from the first weekday set; the fuller a day, the stronger its slate
 * blue, in four fixed steps (1, 2–3, 4–5, 6 and more) that do not hang on the fullest day. The server counts
 * (`/api/events/year`): only the events the views show now, duplicates once, no background calendar. Today has a
 * ring, a day of a calendar with "Tage hervorheben" a red dot; weekends are not tinted here.
 *
 * A click on a day opens it in Day, a click on a month's name that month in Month. For a screen reader each day says
 * its date and how full it is, the months are groups; the days are one stop of the Tab key, the arrows go from day to
 * day and week to week, Enter or Space opens the day. Without a connection the year says it shows once connected.
 * With room enough, the column of coming events stands beside it, as beside the other views.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { weekdayNames } from '../../lib/display'
import { useTitle } from '../../lib/title'
import { useDisplay } from '../../lib/useDisplay'
import { eventsApi, type YearMap } from '../calendars/api'
import { useRevision } from '../calendars/store'
import { EmptyCollection } from '../collections/CollectionMenu'
import { useShown } from '../collections/store'
import { useOffline, watchSlow } from '../offline/connection'
import { OutsideKept } from '../offline/OfflineHint'
import { LoadProblem } from './bits'
import { useWide, ViewColumn } from './CalendarColumn'
import { addDays, format, monthWeeks } from './dates'
import { setViewDay, todayAt, useNarrow, useNow, useViewCommands, useViewDay } from './state'
import { ViewHead } from './ViewHead'
import { addYears, heatStep, yearFit, type HeatStep } from './year'

type Year = { map: YearMap | null; failed: boolean; offline: boolean; retry: () => void }

/**
 * The counts of `year` for what the views show (the collection chosen, "Nur diesen", "All"). Asked again with every
 * change (an event made, moved or deleted, here or elsewhere: EC-21); what came last stays meanwhile.
 */
function useYear(year: number): Year {
  const { zone } = useDisplay()
  const shown = useShown()
  const seen = useRevision()
  const offline = useOffline()
  const [again, setAgain] = useState(0)
  const [state, setState] = useState<{ key: string; map: YearMap } | null>(null)
  const [failed, setFailed] = useState(false)
  const calendars = shown.calendars === null ? null : shown.calendars.join(',')
  const key = `${year}|${zone}|${calendars}|${shown.hidden}`
  const pending = !!shown.pending
  useEffect(() => {
    if (pending || offline) return
    if (calendars === '') {
      setState({ key, map: { year, zone, days: {}, holidays: [] } })
      return
    }
    let live = true
    const abort = typeof AbortController === 'function' ? new AbortController() : null
    watchSlow(eventsApi.year(year, zone, { calendars: calendars === null ? null : calendars.split(',').map(Number), hidden: shown.hidden }, abort?.signal))
      .then((answer) => {
        if (!live) return
        const good = !!answer && typeof answer === 'object' && !!answer.days && typeof answer.days === 'object'
        setState({ key, map: good ? { ...answer, holidays: Array.isArray(answer.holidays) ? answer.holidays : [] } : { year, zone, days: {}, holidays: [] } })
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
  }, [key, year, zone, calendars, shown.hidden, pending, seen, again, offline])
  return { map: state && state.key === key ? state.map : null, failed, offline, retry: () => setAgain((value) => value + 1) }
}

export function YearView() {
  const { t, i18n } = useTranslation()
  const { display, zone } = useDisplay()
  useTitle(t('views.year'))
  const language = i18n.language
  const narrow = useNarrow()
  const now = useNow()
  const today = todayAt(now, zone)
  const [day, setDay] = useViewDay(today)
  const [picking, setPicking] = useState(false)
  const navigate = useNavigate()
  const shown = useShown()
  // Room enough: the coming events of what is shown in a column beside the year, as beside Month, Week and Day.
  const wide = useWide()
  const column = !narrow && wide && !shown.empty && !shown.pending
  const year = Number(day.slice(0, 4))
  const { map, failed, offline, retry } = useYear(year)
  const holidays = useMemo(() => new Set(map?.holidays ?? []), [map])
  // The day that holds the Tab stop among the days: the one last moved to, else the day shown.
  const [active, setActive] = useState<string | null>(null)
  const focused = active && active.startsWith(`${year}-`) ? active : day
  const grid = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const legend = useRef<HTMLUListElement>(null)
  const moved = useRef(false)
  // The whole year in the room there is, without scrolling (EC-22; found while testing): how many months side by side
  // and how wide, from the width and height of the view; the classes below stand in until it is measured.
  const [fit, setFit] = useState<{ columns: number; month: number } | null>(null)
  useEffect(() => {
    const box = scroller.current
    const area = grid.current?.parentElement
    if (!box || !area || typeof ResizeObserver === 'undefined') return
    const measure = () => {
      const top = area.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop
      const below = (legend.current?.offsetHeight ?? 20) + 12 + 16
      const width = area.clientWidth
      const height = box.clientHeight - top - below
      setFit(width > 0 && height > 0 ? yearFit(width, height) : null)
    }
    const watch = new ResizeObserver(measure)
    watch.observe(box)
    watch.observe(area)
    measure()
    return () => watch.disconnect()
  }, [offline])
  useEffect(() => {
    if (!moved.current) return
    moved.current = false
    grid.current?.querySelector<HTMLButtonElement>(`[data-date="${focused}"]`)?.focus()
  }, [focused])

  const step = (direction: -1 | 1) => setDay(addYears(day, direction))
  useViewCommands((command) => {
    if (command === 'pick') setPicking(true)
    else if (command === 'previous' || command === 'next') step(command === 'previous' ? -1 : 1)
  })
  const open = (path: '/day' | '/month', date: string) => {
    setViewDay(date)
    navigate(path)
  }
  // The arrows go from day to day and week to week within the year; Enter and Space are the button's own.
  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const from = (event.target as HTMLElement).dataset?.date
    const by = ({ ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 } as Record<string, number>)[event.key]
    if (!from || by === undefined) return
    // Taken here: the keys of the page (a year back or on) stay out of it.
    event.preventDefault()
    const next = addDays(from, by)
    if (!next.startsWith(`${year}-`)) return
    moved.current = true
    setActive(next)
  }

  const months = Array.from({ length: 12 }, (_, index) => `${year}-${String(index + 1).padStart(2, '0')}-01`)
  const weekdays = weekdayNames(display.week_start, language).map((name) => name.replace('.', '').slice(0, 2))
  const steps: { step: HeatStep; label: string }[] = [
    { step: 0, label: t('views.free') },
    { step: 1, label: '1' },
    { step: 2, label: '2–3' },
    { step: 3, label: '4–5' },
    { step: 4, label: '6+' },
  ]

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 overflow-hidden">
    <div ref={scroller} className="flex h-full min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto px-3 pt-3 pb-4 sm:px-6 sm:pt-5" data-testid="view-year">
      <ViewHead kind="year" title={String(year)} week={null} narrow={narrow} onStep={step} onToday={() => setDay(today)} onJump={() => undefined} day={day} today={today} picking={picking} onPicking={setPicking} onPick={setDay} />
      {failed && <LoadProblem onRetry={retry} />}
      {shown.empty && <EmptyCollection />}
      {offline ? (
        <OutsideKept />
      ) : (
        <>
          <div className="@container">
            <div
              ref={grid}
              className="grid grid-cols-[repeat(2,minmax(0,200px))] gap-x-5 gap-y-4 @[40rem]:grid-cols-[repeat(3,minmax(0,200px))] @[52rem]:grid-cols-[repeat(4,minmax(0,200px))] @[78rem]:grid-cols-[repeat(6,minmax(0,200px))]"
              style={fit ? { gridTemplateColumns: `repeat(${fit.columns}, ${fit.month}px)` } : undefined}
              aria-busy={map === null && !failed}
              onKeyDown={move}
              data-testid="year-grid"
            >
              {months.map((first) => {
                const monthName = format(first, language, { month: 'long' })
                return (
                  <section key={first} role="group" aria-label={format(first, language, { month: 'long', year: 'numeric' })} className="min-w-0" data-testid="year-month">
                    <button
                      type="button"
                      className="nt-wrap mb-1.5 rounded-[6px] text-left text-sm font-semibold text-accent-400 hover:underline"
                      aria-label={t('views.monthAria', { month: format(first, language, { month: 'long', year: 'numeric' }) })}
                      onClick={() => open('/month', first)}
                      data-testid="year-month-name"
                    >
                      {monthName}
                    </button>
                    <div className="mb-1 grid grid-cols-7 gap-[3px] text-center text-[10px] text-mist-500" aria-hidden>
                      {weekdays.map((name, index) => (
                        <span key={index} className="truncate">
                          {name}
                        </span>
                      ))}
                    </div>
                    <div className="grid grid-cols-7 gap-[3px]">
                      {monthWeeks(first, display.week_start)
                        .flat()
                        .map((date) => {
                          if (date.slice(0, 7) !== first.slice(0, 7)) return <span key={date} aria-hidden />
                          const count = map?.days[date] ?? 0
                          const holiday = holidays.has(date)
                          const label = [
                            format(date, language, { weekday: 'long', day: 'numeric', month: 'long' }),
                            count > 0 ? t('views.yearCount', { count }) : t('views.free'),
                            ...(holiday ? [t('views.yearHoliday')] : []),
                          ].join(', ')
                          return (
                            <button
                              key={date}
                              type="button"
                              className="nt-heat"
                              data-step={heatStep(count)}
                              data-date={date}
                              data-today={date === today || undefined}
                              data-holiday={holiday || undefined}
                              tabIndex={date === focused ? 0 : -1}
                              aria-label={label}
                              aria-current={date === today ? 'date' : undefined}
                              title={label}
                              onFocus={() => setActive(date)}
                              onClick={() => open('/day', date)}
                            />
                          )
                        })}
                    </div>
                  </section>
                )
              })}
            </div>
          </div>
          <ul ref={legend} className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-mist-500" aria-label={t('views.yearLegend')} data-testid="year-legend">
            {steps.map((entry) => (
              <li key={entry.step} className="inline-flex items-center gap-1.5">
                <span className="nt-heat-swatch" data-step={entry.step} aria-hidden />
                {entry.label}
              </li>
            ))}
            <li className="inline-flex items-center gap-1.5">
              <span className="nt-heat-swatch" data-step={0} data-holiday aria-hidden />
              {t('views.yearHoliday')}
            </li>
          </ul>
        </>
      )}
    </div>
    {column && <ViewColumn shown={shown} now={now} />}
    </div>
  )
}
