/**
 * "Demnächst" (asked for while testing, 2026-11-24): the coming events of what is shown, day by day from today, as a
 * calm list. Each day with its date sheet and how far it is ("heute", "morgen", "in 5 Tagen"), its background entries
 * quiet beside it (birthdays, holidays), then its events with time, calendar color, title, calendar and place; an
 * event over several days stands on its first day (or today, while it runs) with "bis …". Between two days with free
 * days in between a dotted line says "3 Tage frei", as the app plans it (nextime-ios, G2). Thirty days at first,
 * "Weitere 30 Tage" up to a year.
 *
 * The row of chips above filters by what the views show anyway: "Alles", a collection, or one calendar alone. It sets
 * the same choice as "Sammlung" and "Nur diesen", so the list and the calendar never disagree. A click on an event
 * opens it; without a connection what the installed app kept.
 */
import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { useTitle } from '../../lib/title'
import { useDisplay } from '../../lib/useDisplay'
import type { Occurrence } from '../calendars/api'
import { useCalendars } from '../calendars/store'
import { EmptyCollection } from '../collections/CollectionMenu'
import { useChosen, useCollections, useOnly, useShown } from '../collections/store'
import { OutsideKept } from '../offline/OfflineHint'
import { Dot, ListTitle, LoadProblem, useCalendarMap, useClock, useOpen } from '../views/bits'
import { DateSheet } from '../views/DateSheet'
import { addDays, daysBetween, format } from '../views/dates'
import { daysOf, isBackground, isHoliday } from '../views/layout'
import { todayAt, useNow, useWindow } from '../views/state'

/** Days asked for at first, and with every "Weitere 30 Tage"; at most a year ahead. */
export const UPCOMING_STEP = 30
export const UPCOMING_MAX = 360

type Day = { date: string; events: Occurrence[]; background: Occurrence[] }

export function UpcomingPage() {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const { zone } = useDisplay()
  useTitle(t('nav.upcoming'))
  const now = useNow()
  const today = todayAt(now, zone)
  const [ahead, setAhead] = useState(UPCOMING_STEP)
  const end = addDays(today, ahead)
  const loaded = useWindow(today, end)
  const calendars = useCalendarMap()
  const shown = useShown()
  const open = useOpen()
  const clock = useClock()

  // Each occurrence on its first day, or today while it still runs; background entries apart.
  const days = useMemo(() => {
    const found = new Map<string, Day>()
    for (const row of loaded.rows ?? []) {
      const span = daysOf(row, zone)
      if (span.last < today) continue
      const date = span.first < today ? today : span.first
      if (date >= end) continue
      const day = found.get(date) ?? { date, events: [], background: [] }
      if (isBackground(row, calendars)) day.background.push(row)
      else day.events.push(row)
      found.set(date, day)
    }
    const order = (row: Occurrence) => (row.all_day ? '' : row.start)
    return [...found.values()]
      .map((day) => ({ ...day, events: [...day.events].sort((a, b) => order(a).localeCompare(order(b))) }))
      .sort((a, b) => a.date.localeCompare(b.date))
  }, [loaded.rows, zone, today, end, calendars])

  /** "heute", "morgen", "in 5 Tagen". */
  const distance = (date: string) => {
    const away = daysBetween(today, date)
    if (away === 0) return t('views.column.today')
    if (away === 1) return t('views.column.tomorrow')
    return t('views.column.inDays', { count: away })
  }

  const gap = (from: string, to: string, key: string) => {
    const free = daysBetween(from, to) - 1
    if (free < 1) return null
    return (
      <li key={key} className="flex items-center gap-3 py-1 pl-[52px] text-xs text-mist-500" data-testid="upcoming-gap">
        <span aria-hidden className="h-0 w-8 border-t-2 border-dotted border-ink-600" />
        {t('upcoming.free', { count: free })}
      </li>
    )
  }

  const items: ReactNode[] = []
  let before = addDays(today, -1)
  for (const day of days) {
    items.push(gap(before, day.date, `gap-${day.date}`))
    before = day.date
    const holiday = isHoliday(day.background, calendars)
    items.push(
      <li key={day.date} className="flex gap-3 py-2" data-date={day.date} data-testid="upcoming-day">
        <DateSheet day={day.date} language={language} color={holiday ? 'var(--color-bad-500)' : 'var(--color-accent-500)'} testId="upcoming-sheet" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 pt-1 text-sm">
            <span className="font-semibold text-mist-100">{format(day.date, language, { weekday: 'long', day: 'numeric', month: 'long' })}</span>
            <span className={daysBetween(today, day.date) <= 1 ? 'text-accent-400' : 'text-mist-500'}>{distance(day.date)}</span>
            {day.background.length > 0 && (
              <span className={'min-w-0 truncate text-xs ' + (holiday ? 'nt-holiday-name' : 'text-mist-500')} data-testid="upcoming-background">
                {day.background.map((row) => row.title).join(' · ')}
              </span>
            )}
          </div>
          {day.events.length > 0 && (
            <ul className="mt-1.5 space-y-0.5">
              {day.events.map((row) => {
                const calendar = calendars.get(row.calendar_id)
                const span = daysOf(row, zone)
                const past = !row.all_day && Date.parse(row.end) <= now
                const sub = [
                  calendar?.name,
                  span.last > day.date ? t('views.column.until', { day: format(span.last, language, { day: 'numeric', month: 'long' }) }) : '',
                  row.location,
                ].filter(Boolean)
                return (
                  <li key={`${row.id}-${row.occurrence}`}>
                    <button
                      type="button"
                      className={'flex w-full min-w-0 items-start gap-3 rounded-[9px] px-2 py-1.5 text-left hover:bg-ink-800 ' + (past ? 'opacity-50' : '')}
                      onClick={() => open(row)}
                      data-testid="upcoming-event"
                    >
                      <span className="w-12 shrink-0 pt-px text-[13px] text-mist-500 tabular-nums">
                        {row.all_day || span.first < day.date ? t('views.allDayShort') : clock(Date.parse(row.start))}
                      </span>
                      <span className="mt-[6px]">
                        <Dot color={row.busy_only ? 'gray' : (calendar?.color ?? 'gray')} size={8} />
                      </span>
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="nt-wrap text-[14px] font-medium text-mist-100">
                          <ListTitle row={row} />
                        </span>
                        {sub.length > 0 && <span className="truncate text-xs text-mist-500">{sub.join(' · ')}</span>}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </li>,
    )
  }
  if (days.length > 0) items.push(gap(before, end, 'gap-end'))

  return (
    <div className="h-full min-h-0 w-full overflow-y-auto px-3 pt-3 pb-6 sm:px-6 sm:pt-5" data-testid="view-upcoming">
      <div className="mx-auto flex max-w-[760px] flex-col gap-4">
        <h1 className="text-lg font-bold tracking-tight text-mist-100 sm:text-xl">{t('nav.upcoming')}</h1>
        <UpcomingFilter />
        {loaded.failed && <LoadProblem onRetry={loaded.retry} />}
        {loaded.outside && <OutsideKept />}
        {shown.empty && <EmptyCollection />}
        {loaded.rows === null && !loaded.failed ? (
          <div className="space-y-3" aria-busy>
            {[0, 1, 2].map((index) => (
              <span key={index} className="nt-skeleton h-12 w-full" />
            ))}
          </div>
        ) : days.length === 0 ? (
          <p className="nt-empty" data-testid="upcoming-empty">
            {t('upcoming.empty', { count: ahead })}
          </p>
        ) : (
          <ul data-testid="upcoming-list">{items}</ul>
        )}
        {ahead < UPCOMING_MAX && loaded.rows !== null && (
          <button type="button" className="nt-btn nt-btn-quiet self-start" onClick={() => setAhead((value) => Math.min(value + UPCOMING_STEP, UPCOMING_MAX))} data-testid="upcoming-more">
            {t('upcoming.more', { count: UPCOMING_STEP })}
          </button>
        )}
      </div>
    </div>
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
