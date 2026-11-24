/**
 * The start view "Heute" (TIME-11), like a day plan on the fridge: on top the field "Schnell eintragen", the heading
 * with weekday, date and calendar week, background entries of today beside it; then what comes next or runs now in
 * large, the rest of today below (the past ones folded in a quiet line), and beside it (on a phone below) the next days
 * as a calm list, a free day as "frei". Open invitations count in a sign beside the quick field (TIME-18 AC-1, AC-6)
 * and show dashed as "Einladung: <Titel>".
 *
 * "Today", "past" and "in 25 minutes" follow the clock and zone of the device and move on by themselves (EC-3, EC-11).
 */
import { CalendarDays } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { clockChange } from '../../lib/display'
import { useTitle } from '../../lib/title'
import { useDisplay } from '../../lib/useDisplay'
import type { CalendarEntry, Occurrence } from '../calendars/api'
import { wallOf } from '../calendars/time'
import { EmptyCollection } from '../collections/CollectionMenu'
import { useShown } from '../collections/store'
import { QuickEntry } from '../quick/QuickEntry'
import { InvitationSign } from '../sources/Invitations'
import { BackgroundLine, Dot, ListTitle, LoadProblem, Marks, PersonMark, Twice, useCalendarMap, useClock, useOpen, useSpan, useTitleOf } from './bits'
import { addDays, format, isoWeekOf } from './dates'
import { dayOf, daysOf, isBackground, multiDay, touches } from './layout'
import { setViewDay, todayAt, useCompact, useNarrow, useNow, useViewCommands, useWindow } from './state'
import { DatePicker } from './DatePicker'
import { CalendarColumn, scopeKeyOf, scopeOf } from './CalendarColumn'
import { useTasks } from '../nextasks/hooks'
import { TasksBlock } from '../nextasks/TasksBlock'
import { DeadlinesBlock } from '../nexpaper/DeadlinesBlock'
import { useDeadlines } from '../nexpaper/hooks'
import { WeekJump } from './ViewHead'
import { OutsideKept } from '../offline/OfflineHint'

const NEXT_DAYS = 7

export function TodayView() {
  const { t, i18n } = useTranslation()
  const { display, zone } = useDisplay()
  useTitle(t('nav.today'))
  const now = useNow()
  const today = todayAt(now, zone)
  const [pastOpen, setPastOpen] = useState(false)
  const [jumping, setJumping] = useState(false)
  const [picking, setPicking] = useState(false)
  const dateButton = useRef<HTMLButtonElement>(null)
  // Today and the week after: "Als Nächstes: morgen …" looks there (EC-1); the list on the right asks for itself.
  const loaded = useWindow(today, addDays(today, NEXT_DAYS + 1))
  const tasks = useTasks(today)
  const deadlines = useDeadlines(today)
  const narrow = useNarrow()
  // Below 1024 px the invitations stand in the head of the frame (TIME-28 AC-6).
  const compact = useCompact()
  const calendars = useCalendarMap()
  const shown = useShown()
  const navigate = useNavigate()
  useViewCommands((command) => {
    if (command === 'jump') setJumping(true)
    else if (command === 'pick') setPicking(true)
  })
  // A day chosen from here opens as a day (TIME-12, the jump to a date).
  const toDay = (date: string) => {
    setViewDay(date)
    navigate('/day')
  }

  const language = i18n.language
  const heading = format(today, language, { weekday: 'long', day: 'numeric', month: 'long' })
  const week = isoWeekOf(today).week
  const rows = loaded.rows
  // The night the clocks change (TIME-3 EC-1).
  const [year, monthNumber, dayNumber] = today.split('-').map(Number)
  const change = clockChange({ year, month: monthNumber, day: dayNumber }, zone)
  const background = rows ? rows.filter((row) => isBackground(row, calendars) && touches(row, today, zone)) : []

  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-5 sm:px-6" data-testid="view-today">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <QuickEntry />
        </div>
        {!compact && <InvitationSign />}
      </div>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-mist-100">{t('nav.today')}</h1>
        <span className="text-sm text-mist-500" data-testid="today-date">
          <button
            ref={dateButton}
            type="button"
            className={'rounded-[6px] px-0.5 hover:text-accent-400 ' + (picking ? 'bg-[var(--color-accent-wash)]' : '')}
            title={t('views.pickHint')}
            aria-haspopup="dialog"
            aria-expanded={picking}
            onClick={() => setPicking(!picking)}
            data-testid="today-pick"
          >
            {heading}
          </button>
          {picking && dateButton.current && <DatePicker anchor={dateButton.current} value={today} today={today} onClose={() => setPicking(false)} onPick={(date) => (setPicking(false), toDay(date))} />}
          {display.week_numbers && (
            <>
              {' · '}
              <button type="button" className="hover:text-accent-400" title={t('views.jumpHint')} onClick={() => setJumping(true)} data-testid="week-button">
                <span data-testid="week-number">{t('views.weekNumber', { number: week })}</span>
              </button>
            </>
          )}
        </span>
        <span className="ml-auto min-w-0">
          <BackgroundLine rows={background} calendars={calendars} max={3} testId="background-today" />
        </span>
      </div>
      {change && (
        <p className="text-sm text-mist-500" data-testid="clock-change">
          {t('views.clockChange', { from: change.from, to: change.to })}
        </p>
      )}
      {loaded.failed && <LoadProblem onRetry={loaded.retry} />}
      {loaded.outside && <OutsideKept />}
      {shown.empty ? (
        <EmptyCollection />
      ) : !rows ? (
        <Skeleton />
      ) : (
        <div className="nt-today-grid">
          <div className="min-w-0 space-y-3.5">
            <TodayColumn rows={rows} now={now} today={today} calendars={calendars} pastOpen={pastOpen} onPast={() => setPastOpen(!pastOpen)} />
            {/* Tasks of nextasks due today or overdue, under today's events (TIME-25 AC-4, AC-6). */}
            <TasksBlock tasks={(tasks.tasks ?? []).filter((task) => task.day <= today)} today={today} problem={tasks.problem} notices={tasks.notices} onSeen={tasks.reload} phone={narrow} />
            {/* Deadlines of nexpaper of today or overdue, as the tasks. */}
            <DeadlinesBlock deadlines={(deadlines.deadlines ?? []).filter((one) => one.day <= today)} today={today} problem={deadlines.problem} phone={narrow} />
          </div>
          <CalendarColumn key={`${scopeKeyOf(shown)}-${today}`} scope={scopeOf(shown)} now={now} mode="next" />
        </div>
      )}
      {jumping && (
        <WeekJump
          from={today}
          today={today}
          onClose={() => setJumping(false)}
          onJump={(monday) => {
            setJumping(false)
            setViewDay(monday)
            navigate('/week')
          }}
          onDate={(date) => {
            setJumping(false)
            toDay(date)
          }}
        />
      )}
    </div>
  )
}

function Skeleton() {
  return (
    <div className="nt-today-grid" data-testid="today-loading" aria-hidden>
      <div className="space-y-3">
        <span className="nt-skeleton h-24 rounded-[14px]" />
        <span className="nt-skeleton h-3 w-3/4" />
        <span className="nt-skeleton h-3 w-3/5" />
        <span className="nt-skeleton h-3 w-4/5" />
      </div>
      <div className="space-y-3">
        <span className="nt-skeleton h-3 w-2/5" />
        <span className="nt-skeleton h-3 w-3/4" />
        <span className="nt-skeleton h-3 w-1/2" />
      </div>
    </div>
  )
}

/** "in 40 minutes", "1 hour", "2 h 15 min". */
function TodayColumn({ rows, now, today, calendars, pastOpen, onPast }: {
  rows: Occurrence[]
  now: number
  today: string
  calendars: Map<number, CalendarEntry>
  pastOpen: boolean
  onPast: () => void
}) {
  const { t, i18n } = useTranslation()
  const { zone } = useDisplay()
  const titleOf = useTitleOf()
  const clock = useClock()
  const span = useSpan()
  const open = useOpen()
  const mine = rows.filter((row) => !isBackground(row, calendars) && touches(row, today, zone))
  // Whole days and long events stand on top as a line, never as "next" (EC-2, EC-14).
  const lines = mine.filter((row) => row.all_day || multiDay(row, zone))
  const timed = mine.filter((row) => !row.all_day && !multiDay(row, zone))
  const past = timed.filter((row) => Date.parse(row.end) <= now)
  const running = timed.filter((row) => Date.parse(row.start) <= now && Date.parse(row.end) > now)
  const coming = timed.filter((row) => Date.parse(row.start) > now)
  let next: Occurrence[] = []
  let label = ''
  if (running.length) {
    next = [running[0]]
    label = t('views.running', { span: span(Math.max(1, Math.round((Date.parse(running[0].end) - now) / 60_000))) })
  } else if (coming.length) {
    const first = Date.parse(coming[0].start)
    next = coming.filter((row) => Date.parse(row.start) === first)
    const minutes = Math.max(1, Math.round((first - now) / 60_000))
    label = minutes < 60 ? t('views.nextIn', { count: minutes }) : t('views.nextAt', { time: clock(first) })
  }
  const rest = [...running.slice(1), ...coming.filter((row) => !next.includes(row))]
  const later = rows.find((row) => !isBackground(row, calendars) && !row.all_day && Date.parse(row.start) > now && daysOf(row, zone).first > today)

  const pastLine = past.length > 0 && (
    <>
      <button type="button" className="flex w-full items-center gap-2.5 border-b border-line px-4 py-2.5 text-left text-sm text-mist-500" aria-expanded={pastOpen} onClick={onPast} data-testid="past">
        {next.length ? (
          <>
            <span className="nt-wrap min-w-0 flex-1">{t('views.past', { titles: past.map((row) => titleOf(row)).join(', ') })}</span>
            <span>
              {past.length} {pastOpen ? '▴' : '▾'}
            </span>
          </>
        ) : (
          <span>
            {t('views.pastCount', { count: past.length })} {pastOpen ? '▴' : '▾'}
          </span>
        )}
      </button>
      {pastOpen && past.map((row) => <TodayRow key={key(row)} row={row} calendars={calendars} />)}
    </>
  )

  return (
    <div className="min-w-0 space-y-3.5" data-testid="today-column">
      {next.length > 0 ? (
        next.map((row, index) => (
          <button key={key(row)} type="button" className="flex w-full items-center gap-4 rounded-[14px] bg-[var(--color-accent-wash)] px-5 py-4 text-left" onClick={() => open(row)} data-testid="next">
            <span className="flex min-w-0 flex-1 flex-col gap-1">
              {index === 0 && (
                <span className="text-xs font-semibold tracking-wider text-accent-400 uppercase" data-testid="next-label">
                  {label}
                </span>
              )}
              <strong className="nt-wrap text-lg font-bold text-mist-100">{titleOf(row)}</strong>
              <span className="text-sm text-mist-500">
                {clock(Date.parse(row.start))}–{clock(Date.parse(row.end))}
                {row.location ? ` · ${row.location}` : ''}
                <span className="nt-hide-narrow">{` · ${calendars.get(row.calendar_id)?.name ?? ''}`}</span>
                <Marks row={row} calendar={calendars.get(row.calendar_id)} />
              </span>
            </span>
            {index === 0 && row.person && (
              <span className="flex shrink-0 flex-col items-center gap-1 text-[11px] text-mist-500" data-testid="next-person">
                <PersonMark person={row.person} size={30} />
                <span>{row.person.name}</span>
              </span>
            )}
          </button>
        ))
      ) : (
        <div className="nt-empty py-6" data-testid="nothing-more">
          <CalendarDays className="h-6 w-6 text-mist-500" strokeWidth={1.6} aria-hidden />
          <strong className="text-base text-mist-100" data-testid="nothing-more-title">
            {timed.length || lines.length ? t('views.nothingMore') : t('views.emptyToday')}
          </strong>
          {later && (
            <span className="text-sm">
              {t('views.nextLater', {
                when: `${daysOf(later, zone).first === addDays(today, 1) ? t('views.tomorrowWord') : format(wallOf(Date.parse(later.start), zone).date, i18n.language, { weekday: 'short', day: 'numeric', month: 'numeric' })} ${clock(Date.parse(later.start))}`,
                title: titleOf(later),
              })}
            </span>
          )}
        </div>
      )}
      {(pastLine || lines.length > 0 || rest.length > 0) && (
        <div className="overflow-hidden rounded-[12px] border border-ink-700 bg-ink-850" data-testid="today-list">
          {pastLine}
          {lines.map((row) => (
            <TodayRow key={key(row)} row={row} calendars={calendars} when={multiDay(row, zone) ? t('views.dayOf', dayOf(row, today, zone)) : t('views.allDay')} />
          ))}
          {rest.map((row) => (
            <TodayRow key={key(row)} row={row} calendars={calendars} />
          ))}
        </div>
      )}
    </div>
  )
}

function key(row: Occurrence): string {
  return `${row.id}-${row.occurrence ?? ''}`
}

function TodayRow({ row, calendars, when }: { row: Occurrence; calendars: Map<number, CalendarEntry>; when?: ReactNode }) {
  const clock = useClock()
  const open = useOpen()
  const calendar = calendars.get(row.calendar_id)
  return (
    <button type="button" className="flex w-full items-center gap-3 border-b border-line px-4 py-2.5 text-left text-sm last:border-b-0 hover:bg-ink-800" onClick={() => open(row)} data-testid="today-row">
      <span className="w-[6.5rem] shrink-0 text-xs text-mist-500">{when ?? `${clock(Date.parse(row.start))}–${clock(Date.parse(row.end))}`}</span>
      <Dot color={calendar?.color ?? 'gray'} />
      <span className={'nt-wrap min-w-0 flex-1 ' + (row.busy_only ? 'text-mist-500' : 'text-mist-100')}>
        <ListTitle row={row} /> <Twice row={row} />
        <Marks row={row} calendar={calendar} />
      </span>
      <span className="nt-hide-narrow shrink-0 text-xs text-mist-500">{calendar?.name}</span>
    </button>
  )
}
