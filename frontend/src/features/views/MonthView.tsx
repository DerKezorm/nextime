/**
 * The month (TIME-12 AC-5, AC-10, AC-14, EC-12): the column of calendar weeks on the left when they are on, each a
 * button to that week; background entries small beside the day's number; days outside the month quiet; the first of
 * the next month names it ("1. Dez."); each event with its dot, time and title, at most three, then "+n more". A
 * month takes as many rows as it needs, six at most. On a phone each day shows dots and below the list of the day
 * chosen. A click on the free space of a day opens "Neuer Termin" on that day (TIME-12, "in einen freien Platz
 * klicken"): today at the next full hour, other days at 09:00, the usual length; not on a phone (the plus of the bar
 * there), not without a connection (TIME-28 AC-3).
 */
import { useMemo, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { weekdayNames } from '../../lib/display'
import { useTitle } from '../../lib/title'
import { colorVar } from '../calendars/Swatch'
import { useDisplay } from '../../lib/useDisplay'
import type { Occurrence } from '../calendars/api'
import { usePanel } from '../calendars/store'
import { nextFullHour } from '../calendars/time'
import { useOffline } from '../offline/connection'
import { EmptyCollection } from '../collections/CollectionMenu'
import { useShown } from '../collections/store'
import { DayList, Dot, invited, LoadProblem, useCalendarMap, useClock, useOpen, useTitleOf } from './bits'
import { addDays, addMonths, format, isoWeekOf, isWeekend, monthWeeks, weekday } from './dates'
import { daysOf, isBackground, isHoliday } from './layout'
import { setViewDay, todayAt, useNarrow, useNow, useViewCommands, useViewDay, useWindow } from './state'
import { useWide, ViewColumn } from './CalendarColumn'
import { ViewHead, WeekJump } from './ViewHead'
import { OutsideKept } from '../offline/OfflineHint'
import type { Task } from '../nextasks/api'
import { useTasks } from '../nextasks/hooks'
import { TaskBox, TaskRow, TaskTitle } from '../nextasks/TaskItem'
import type { Deadline } from '../nexpaper/api'
import { DeadlineRow, MonthDeadline } from '../nexpaper/DeadlineItem'
import { useDeadlines } from '../nexpaper/hooks'

const PER_DAY = 3

export function MonthView() {
  const { t, i18n } = useTranslation()
  const { display, zone } = useDisplay()
  useTitle(t('nav.month'))
  const language = i18n.language
  const narrow = useNarrow()
  const now = useNow()
  const today = todayAt(now, zone)
  const [day, setDay] = useViewDay(today)
  const [jumping, setJumping] = useState(false)
  const [picking, setPicking] = useState(false)
  const [list, setList] = useState<{ date: string; rows: Occurrence[]; tasks: Task[]; deadlines: Deadline[] } | null>(null)
  const navigate = useNavigate()
  const weeks = monthWeeks(day, display.week_start)
  const first = weeks[0][0]
  const last = weeks[weeks.length - 1][6]
  const loaded = useWindow(first, addDays(last, 1))
  const calendars = useCalendarMap()
  const shown = useShown()
  // Tasks of nextasks on their due day (TIME-25 month, asked for while testing, 2026-11-23): after the day's events,
  // counted in "+n weitere"; overdue ones on their own day only, coming days of a repeating task as previews.
  const { tasks } = useTasks(last, { from: first, previews: true })
  const tasksOn = (date: string) => (tasks ?? []).filter((task) => task.day === date)
  // Deadlines of nexpaper on their day to act by, after the tasks and as they are (overdue ones on their own day).
  const { deadlines } = useDeadlines(last, { from: first })
  const deadlinesOn = (date: string) => (deadlines ?? []).filter((deadline) => deadline.day === date)
  // Room enough: the events of what is shown in a column beside the month (TIME-12, TIME-13).
  const wide = useWide()
  const column = !narrow && wide && !shown.empty && !shown.pending
  const open = useOpen()
  const { open: openPanel } = usePanel()
  const offline = useOffline()
  const titleOf = useTitleOf()
  // A new event on a day of the month: what a click in the free space of Day or Week opens, at the next full hour today.
  const newOn = (date: string) => {
    const next = nextFullHour(zone, now)
    // Today after 23:00 the next full hour is tomorrow: the event stays on the day clicked, its last hour
    // (review of the additions, 2026-11-23); never 09:00 of a day already past.
    if (date === today && next.date !== date) return openPanel({ kind: 'new', start: `${date}T23:00`, end: `${date}T23:59` })
    openPanel({ kind: 'new', start: `${date}T${date === today ? next.time : '09:00'}` })
  }
  const clock = useClock()
  const month = day.slice(0, 7)
  // Events with a dot, or on a ground in their calendar's color (Einstellungen › Darstellung); the phone keeps its dots.
  const tinted = display.month_style === 'tint'

  const step = (direction: -1 | 1) => setDay(addMonths(day, direction))
  useViewCommands((command) => {
    if (command === 'jump') setJumping(true)
    else if (command === 'pick') setPicking(true)
    else step(command === 'previous' ? -1 : 1)
  })

  // The occurrences of each day of the grid, sorted out once: asking every row for every one of the 42 days turned a
  // full month into a second of work (found while testing the year, 2026-11-24).
  const byDay = useMemo(() => {
    const found = new Map<string, Occurrence[]>()
    for (const row of loaded.rows ?? []) {
      const span = daysOf(row, zone)
      const end = span.last > last ? last : span.last
      for (let date = span.first < first ? first : span.first; date <= end; date = addDays(date, 1)) {
        const list = found.get(date)
        if (list) list.push(row)
        else found.set(date, [row])
      }
    }
    return found
  }, [loaded.rows, zone, first, last])
  const rowsOf = (date: string) => byDay.get(date) ?? []
  const names = weekdayNames(display.week_start, language)
  // The ISO week of the row's Thursday, whatever day the week begins (TIME-3 EC-13).
  const week = (days: string[]) => isoWeekOf(days.find((date) => weekday(date) === 4) ?? days[0]).week
  const toWeek = (start: string) => {
    setViewDay(start)
    navigate('/week')
  }
  const chosen = rowsOf(day)

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 overflow-hidden">
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-y-auto px-3 pt-3 pb-4 sm:px-6 sm:pt-5" data-testid="view-month">
      <ViewHead kind="month" title={format(`${month}-01`, language, { month: 'long', year: 'numeric' })} week={null} narrow={narrow} onStep={step} onToday={() => setDay(today)} onJump={() => setJumping(true)} day={day} today={today} picking={picking} onPicking={setPicking} onPick={setDay} />
      {loaded.failed && <LoadProblem onRetry={loaded.retry} />}
      {loaded.outside && <OutsideKept />}
      {shown.empty && <EmptyCollection />}
      <div className="nt-month text-xs text-mist-500" style={{ '--kw': display.week_numbers ? '34px' : '0px' } as CSSProperties}>
        <span data-testid={display.week_numbers ? 'week-column-head' : undefined}>{display.week_numbers ? t('views.weekShort') : ''}</span>
        {names.map((name) => (
          <span key={name} className="truncate pb-1 pl-2" title={name} data-testid="month-weekday">
            {name}
          </span>
        ))}
      </div>
      <div className="nt-month flex-1 border-t border-ink-700" style={{ '--kw': display.week_numbers ? '34px' : '0px' } as CSSProperties} data-testid="month-grid">
        {weeks.map((days) => (
          <div key={days[0]} className="contents" data-testid="month-week">
            <span className="border-b border-line pt-1.5 text-[11px]">
              {display.week_numbers && (
                <button type="button" className="text-mist-500 hover:text-accent-400" aria-label={t('views.weekAria', { number: week(days) })} onClick={() => toWeek(days[0])} data-testid="month-week-number">
                  {week(days)}
                </button>
              )}
            </span>
            {days.map((date) => {
              const here = rowsOf(date)
              const background = here.filter((row) => isBackground(row, calendars))
              const entries = here.filter((row) => !isBackground(row, calendars))
              const holiday = isHoliday(background, calendars)
              const dayTasks = tasksOn(date)
              const taskRoom = Math.max(0, PER_DAY - entries.length)
              const dayDeadlines = deadlinesOn(date)
              const deadlineRoom = Math.max(0, taskRoom - dayTasks.length)
              const hiddenCount = Math.max(0, entries.length - PER_DAY) + Math.max(0, dayTasks.length - taskRoom) + Math.max(0, dayDeadlines.length - deadlineRoom)
              const outside = date.slice(0, 7) !== month
              const number = Number(date.slice(8)) === 1 && outside ? format(date, language, { day: 'numeric', month: 'short' }) : String(Number(date.slice(8)))
              const classes = ['nt-month-cell', isWeekend(date) ? 'nt-weekend' : '', holiday ? 'nt-holiday' : '', date === today ? 'nt-today-column' : '', outside ? 'opacity-60' : ''].join(' ')
              return (
                <div
                  key={date}
                  className={classes}
                  data-date={date}
                  data-outside={outside || undefined}
                  data-holiday={holiday || undefined}
                  data-addable={!narrow && !offline ? '' : undefined}
                  onClick={(event) => {
                    // The day's number, an event, "+n" are buttons of their own; the free space makes a new event.
                    if (narrow || offline || (event.target as HTMLElement).closest('button, [data-task], [data-deadline]')) return
                    newOn(date)
                  }}
                  data-testid="month-day"
                >
                  <div className="flex min-w-0 items-center gap-1.5">
                    <button
                      type="button"
                      className={'rounded-[10px] px-1 text-xs font-semibold ' + (date === today ? 'bg-accent-500 px-1.5 text-on-accent' : outside ? 'text-mist-500' : 'text-mist-100') + (narrow && date === day && date !== today ? ' ring-1 ring-accent-500' : '')}
                      aria-label={t('views.dayAria', { day: format(date, language, { weekday: 'long', day: 'numeric', month: 'long' }) })}
                      aria-current={date === today ? 'date' : undefined}
                      onClick={() => (narrow ? setDay(date) : (setViewDay(date), navigate('/day')))}
                      data-testid="month-day-number"
                    >
                      {number}
                    </button>
                    {!narrow && (
                      <span className={'min-w-0 truncate text-[10.5px] ' + (holiday ? 'nt-holiday-name' : 'text-mist-500')} title={background.map((row) => row.title).join(' · ')} data-testid="month-background">
                        {background.map((row) => row.title).join(' · ')}
                      </span>
                    )}
                  </div>
                  {narrow ? (
                    <span className="flex flex-wrap gap-[3px]" data-testid="month-dots">
                      {entries.slice(0, 4).map((row) => (
                        <Dot key={`${row.id}-${row.occurrence}`} color={calendars.get(row.calendar_id)?.color ?? 'gray'} size={6} />
                      ))}
                      {dayTasks.length > 0 && <span className="mt-px inline-block h-[6px] w-[6px] shrink-0 rounded-[1.5px] border border-mist-400" aria-hidden data-testid="month-task-dot" />}
                      {dayDeadlines.length > 0 && <span className="mt-px inline-block h-[6px] w-[6px] shrink-0 rotate-45 border border-mist-400" aria-hidden data-testid="month-deadline-dot" />}
                    </span>
                  ) : (
                    <>
                      {entries.slice(0, PER_DAY).map((row) => {
                        const { first: begins, last: ends } = daysOf(row, zone)
                        return (
                          <button
                            key={`${row.id}-${row.occurrence}`}
                            type="button"
                            className={'nt-month-event ' + (tinted ? 'nt-tint ' : '') + (row.busy_only ? 'text-mist-500' : '')}
                            style={tinted ? ({ '--k': colorVar(row.busy_only ? 'gray' : (calendars.get(row.calendar_id)?.color ?? 'gray')) } as CSSProperties) : undefined}
                            data-style={tinted ? 'tint' : 'dot'}
                            title={titleOf(row)}
                            onClick={() => open(row)}
                            data-long={ends > begins ? `${begins === date ? 'start' : ''}${ends === date ? 'end' : ''}` || 'middle' : undefined}
                            data-testid="month-event"
                          >
                            {!tinted && <Dot color={calendars.get(row.calendar_id)?.color ?? 'gray'} size={6} />}
                            {!row.all_day && begins === date && <span className="shrink-0 text-mist-500">{clock(Date.parse(row.start))}</span>}
                            <span className={'min-w-0 truncate ' + (invited(row) ? 'nt-invitation-text' : '')}>{titleOf(row)}</span>
                            {row.waiting && <span className="nt-waiting">{t('views.waiting')}</span>}
                          </button>
                        )
                      })}
                      {dayTasks.slice(0, taskRoom).map((task) => (
                        <div key={`${task.id}-${task.day}`} className="nt-month-event text-mist-400" data-task={task.id} data-testid="month-task">
                          <TaskBox task={task} small />
                          <TaskTitle task={task} className="text-mist-400" />
                        </div>
                      ))}
                      {dayDeadlines.slice(0, deadlineRoom).map((deadline) => (
                        <MonthDeadline key={deadline.id} deadline={deadline} />
                      ))}
                      {hiddenCount > 0 && (
                        <button type="button" className="text-left text-[11px] font-semibold text-accent-400" onClick={() => setList({ date, rows: entries, tasks: dayTasks, deadlines: dayDeadlines })} data-testid="month-more">
                          {t('views.moreCount', { count: hiddenCount })}
                        </button>
                      )}
                    </>
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      {narrow && (
        <section className="space-y-1.5 pt-2" aria-label={format(day, language, { weekday: 'long', day: 'numeric', month: 'long' })} data-testid="month-day-list">
          <strong className="text-sm text-mist-100">{format(day, language, { weekday: 'long', day: 'numeric', month: 'long' })}</strong>
          <p className="text-xs text-mist-500">{chosen.filter((row) => isBackground(row, calendars)).map((row) => `· ${row.title}`).join(' ')}</p>
          {chosen.filter((row) => !isBackground(row, calendars)).length === 0 && !tasksOn(day).length && !deadlinesOn(day).length ? (
            <p className="text-sm text-mist-500">{t('views.free')}</p>
          ) : (
            chosen
              .filter((row) => !isBackground(row, calendars))
              .map((row) => (
                <button key={`${row.id}-${row.occurrence}`} type="button" className="flex w-full items-center gap-2.5 py-1 text-left text-[13.5px]" onClick={() => open(row)} data-testid="month-day-row">
                  <span className="w-11 shrink-0 text-mist-500">{row.all_day ? t('views.allDayShort') : clock(Date.parse(row.start))}</span>
                  <Dot color={calendars.get(row.calendar_id)?.color ?? 'gray'} />
                  <span className="nt-wrap min-w-0 flex-1">{titleOf(row)}</span>
                </button>
              ))
          )}
          {tasksOn(day).map((task) => (
            <TaskRow key={`${task.id}-${task.day}`} task={task} />
          ))}
          {deadlinesOn(day).map((deadline) => (
            <DeadlineRow key={deadline.id} deadline={deadline} />
          ))}
        </section>
      )}
      {list && <DayList date={list.date} rows={list.rows} tasks={list.tasks} deadlines={list.deadlines} onClose={() => setList(null)} onDay={() => (setList(null), setViewDay(list.date), navigate('/day'))} />}
      {jumping && (
        <WeekJump
          from={addDays(weeks[0][0], 3)}
          today={today}
          onClose={() => setJumping(false)}
          onJump={(monday) => {
            setJumping(false)
            setDay(monday)
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
