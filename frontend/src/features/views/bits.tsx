/* eslint-disable react-refresh/only-export-components */
/**
 * The small pieces every view draws the same way (TIME-14): the color dot of a calendar, the person at an event of a
 * shared calendar (picture, else the initial), "Busy (Anna)" for a calendar shared as busy only, "in 2 calendars",
 * background entries small in one quiet line with "+n" (EC-10), the list of all events of a day, and times on the
 * account's clock. Titles and names are always text (EC-11). From connected sources: "wartet" at a change that has not
 * arrived yet (TIME-15 EC-2), "Stand 15:52" at an event of a source that does not answer (EC-5), and an invitation
 * dashed (TIME-18 AC-1, AC-7).
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Avatar } from '../../components/Avatar'
import { Dialog } from '../../components/Dialog'
import { useDisplay } from '../../lib/useDisplay'
import type { CalendarEntry, Occurrence, Person } from '../calendars/api'
import { colorVar } from '../calendars/Swatch'
import { useCalendars, usePanel, type PanelTarget } from '../calendars/store'
import { clockText, dayName, wallOf } from '../calendars/time'
import { useStamp } from '../sources/store'
import type { Task } from '../nextasks/api'
import { TaskRow } from '../nextasks/TaskItem'
import type { Deadline } from '../nexpaper/api'
import { DeadlineRow } from '../nexpaper/DeadlineItem'

export function useCalendarMap(): Map<number, CalendarEntry> {
  const { list } = useCalendars()
  return useMemo(() => new Map((list?.calendars ?? []).map((entry) => [entry.id, entry])), [list])
}

export function Dot({ color, size = 8 }: { color: string; size?: number }) {
  return <span aria-hidden className="inline-block shrink-0 rounded-full" style={{ width: size, height: size, background: colorVar(color) }} data-color={color} />
}

export function PersonMark({ person, size = 22 }: { person: Person; size?: number }) {
  return <Avatar accountId={person.id} name={person.name} stamp={person.avatar} color={person.color} size={size} />
}

/** The title as the views write it: "Busy (Anna)" without title for busy only (AC-5). */
export function useTitleOf(): (row: Occurrence, withName?: boolean) => string {
  const { t } = useTranslation()
  return (row, withName = true) =>
    row.busy_only ? (withName && row.person ? t('views.busyBy', { name: row.person.name }) : t('views.busy')) : row.title
}

/** Times of the day on the account's clock in the zone shown. */
export function useClock(): (ms: number) => string {
  const { zone, twelve, language } = useDisplay()
  return (ms) => clockText(wallOf(ms, zone).time, language, twelve)
}

/** What the side panel opens for an occurrence: for one shown once from several calendars, the first one the member
 * may change, with all of them along (TIME-14 EC-2, EC-3). */
export function targetOf(row: Occurrence): PanelTarget {
  const sources = row.sources && row.sources.length > 1 ? row.sources : undefined
  const first = sources?.find((source) => source.writable) ?? null
  return { kind: 'open', id: first?.id ?? row.id, occurrence: first ? first.occurrence : row.occurrence, sources }
}

export function useOpen(): (row: Occurrence) => void {
  const { open } = usePanel()
  return (row) => open(targetOf(row))
}

/** "in 2 calendars" beside an event shown once (TIME-14 AC-3). */
export function Twice({ row }: { row: Occurrence }) {
  const { t } = useTranslation()
  if (!row.sources || row.sources.length < 2) return null
  return (
    <span className="nt-twice" data-testid="in-calendars">
      {t('views.inCalendars', { count: row.sources.length })}
    </span>
  )
}

/** Background entries of a day, small and quiet in one line: what does not fit as "+n" (TIME-14 AC-1, EC-10). */
export function BackgroundLine({ rows, calendars, max = 2, testId = 'background' }: { rows: Occurrence[]; calendars: Map<number, CalendarEntry>; max?: number; testId?: string }) {
  const [all, setAll] = useState(false)
  if (!rows.length) return null
  const shown = all ? rows : rows.slice(0, max)
  return (
    <span className="nt-background" data-testid={testId}>
      {shown.map((row) => (
        <span key={`${row.id}-${row.occurrence}`} className="nt-background-entry" data-holiday={calendars.get(row.calendar_id)?.holiday || undefined} title={row.title}>
          <Dot color={calendars.get(row.calendar_id)?.color ?? 'gray'} size={6} />
          <span className="min-w-0 truncate">{row.title}</span>
        </span>
      ))}
      {!all && rows.length > max && (
        <button type="button" className="nt-background-more" onClick={() => setAll(true)} data-testid="background-more">
          +{rows.length - max}
        </button>
      )}
    </span>
  )
}

/** Every event of a day in a list, from "+n more" in the month or a full row (TIME-12 AC-4, AC-5, EC-4). */
export function DayList({ date, rows, tasks = [], deadlines = [], onClose, onDay }: { date: string; rows: Occurrence[]; tasks?: Task[]; deadlines?: Deadline[]; onClose: () => void; onDay?: () => void }) {
  const { t, i18n } = useTranslation()
  const calendars = useCalendarMap()
  const open = useOpen()
  const clock = useClock()
  const titleOf = useTitleOf()
  return (
    <Dialog title={dayName(date, i18n.language)} onClose={onClose}>
      <ul className="space-y-1" data-testid="day-list">
        {rows.map((row) => (
          <li key={`${row.id}-${row.occurrence}`}>
            <button
              type="button"
              className="flex w-full items-center gap-3 rounded-[9px] px-2 py-2 text-left text-sm hover:bg-ink-800"
              onClick={() => {
                onClose()
                open(row)
              }}
            >
              <span className="w-24 shrink-0 text-xs text-mist-500">{row.all_day ? t('views.allDay') : `${clock(Date.parse(row.start))}–${clock(Date.parse(row.end))}`}</span>
              <Dot color={calendars.get(row.calendar_id)?.color ?? 'gray'} />
              <span className="nt-wrap min-w-0 flex-1 text-mist-100">{titleOf(row)}</span>
            </button>
          </li>
        ))}
        {tasks.map((task) => (
          <li key={`${task.id}-${task.day}`} className="px-2 py-1.5">
            <TaskRow task={task} />
          </li>
        ))}
        {deadlines.map((deadline) => (
          <li key={`deadline-${deadline.id}`} className="px-2 py-1.5">
            <DeadlineRow deadline={deadline} />
          </li>
        ))}
      </ul>
      <div className="mt-4 flex justify-end gap-2">
        {onDay && (
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onDay}>
            {t('views.showDay')}
          </button>
        )}
        <button type="button" className="nt-btn nt-btn-accent" onClick={onClose}>
          {t('common.done')}
        </button>
      </div>
    </Dialog>
  )
}

/** The quiet failure of a view (design system): the cause, a calming sentence, "Try again". */
export function LoadProblem({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 rounded-[9px] bg-[var(--color-bad-wash)] px-3 py-2 text-sm text-bad-500" data-testid="load-problem">
      <span className="min-w-0 flex-1">{t('views.loadFailed')}</span>
      <button type="button" className="nt-btn nt-btn-quiet text-sm" onClick={onRetry}>
        {t('views.retry')}
      </button>
    </div>
  )
}

/** "wartet" and "Stand 15:52" beside an event (TIME-15 EC-2, EC-5). */
export function Marks({ row, calendar }: { row: Occurrence; calendar?: CalendarEntry }) {
  const { t } = useTranslation()
  const stamp = useStamp()
  const away = calendar?.origin?.state === 'unreachable' && calendar.origin.synced_at
  if (!row.waiting && !away) return null
  return (
    <>
      {' '}
      {row.waiting && (
        <span className="nt-waiting" data-testid="waiting">
          {t('views.waiting')}
        </span>
      )}
      {away && (
        <span className="nt-state-of" data-testid="state-of">
          {t('views.stateOf', { when: stamp(calendar.origin!.synced_at) })}
        </span>
      )}
    </>
  )
}

/** Whether an event shows as an invitation: open, or answered "maybe" (TIME-18 AC-7). */
export function invited(row: Occurrence): boolean {
  return row.invitation?.state === 'open' || row.invitation?.state === 'tentative'
}

/** The title of an invitation in a list: "Einladung: Elternabend" dashed while open, dashed without the word after
 * "Vielleicht" (TIME-18 AC-1, AC-7). */
export function ListTitle({ row }: { row: Occurrence }) {
  const { t } = useTranslation()
  const titleOf = useTitleOf()
  if (row.invitation?.state === 'open')
    return (
      <span className="nt-invitation-text" data-testid="invitation-title">
        {t('invitations.titleOpen', { title: row.title })}
      </span>
    )
  if (row.invitation?.state === 'tentative')
    return (
      <span className="nt-invitation-text" data-testid="invitation-title">
        {row.title}
      </span>
    )
  return <>{titleOf(row)}</>
}
