/* eslint-disable react-refresh/only-export-components */
/**
 * The trash (TIME-23, drawing "T23-Papierkorb"), built like nexnotes' trash: on top the title, the deadline ("Gelöschtes
 * bleibt 30 Tage") and "Papierkorb leeren …"; then the entries, newest first: color, title (a calendar "Kalender „…“
 * mit n Terminen", a series "(Serie, ab 2. Nov)"), its calendar and who deleted it when, or that its calendar is gone
 * and a target will be asked for; the time left, orange in the last seven days and always as text (AC-6, EC-17); and
 * "Wiederherstellen". At the end the sentence about iCloud and Google (AC-5). Titles and names are text (EC-5); long
 * ones are cut with the whole in the tooltip (EC-4). The list loads on as it scrolls (EC-4).
 */
import { CalendarDays, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink } from 'react-router-dom'

import { Dialog } from '../../components/Dialog'
import { problemText } from '../../lib/errors'
import { showNotice } from '../../lib/notice'
import { useTitle } from '../../lib/title'
import { useDisplay } from '../../lib/useDisplay'
import { ApiError } from '../../api/client'
import { colorVar } from '../calendars/Swatch'
import { announce, useCalendars } from '../calendars/store'
import { addDays, clockText, wallOf } from '../calendars/time'
import { Dot } from '../views/bits'
import { useNow } from '../views/state'
import { useOffline } from '../offline/connection'
import { trashApi, type TrashEvent, type TrashItem, type TrashList, type TrashPart } from './api'

export const TRASH_PATH = '/trash'

/** "Papierkorb" at the end of the sidebar's places, under a line, as in nexnotes. */
export function TrashEntry({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation()
  return (
    <NavLink
      to={TRASH_PATH}
      onClick={onNavigate}
      data-testid="trash-entry"
      className={({ isActive }) =>
        'flex h-8 items-center gap-2 rounded-lg px-2.5 text-[13.5px] transition-colors ' + (isActive ? 'nt-chosen font-semibold' : 'text-mist-300 hover:bg-ink-800 hover:text-mist-100')
      }
    >
      <Trash2 className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
      {t('trash.title')}
    </NavLink>
  )
}

const clean = (text: string) => text.replace(/\.$/, '')

/** "Fr 27. Nov" or "Fri, Nov 27": the day of an event in the member's zone (EC-14). */
export function shortDay(date: string, language: string, thisYear: string): string {
  const [year, month, day] = date.split('-').map(Number)
  const at = new Date(Date.UTC(year, month - 1, day))
  const weekday = clean(new Intl.DateTimeFormat(language, { weekday: 'short', timeZone: 'UTC' }).format(at))
  const rest = new Intl.DateTimeFormat(language, { day: 'numeric', month: 'short', ...(date.slice(0, 4) !== thisYear ? { year: 'numeric' } : {}), timeZone: 'UTC' })
    .formatToParts(at)
    .map((part) => (part.type === 'month' ? clean(part.value) : part.value))
    .join('')
  return language.startsWith('de') ? `${weekday} ${rest}` : `${weekday}, ${rest}`
}

function eventDay(item: TrashEvent | TrashPart, zone: string): string {
  return item.all_day ? item.start.slice(0, 10) : wallOf(Date.parse(item.start), zone).date
}

/** The first line: what it is. */
export function titleLine(item: TrashItem, t: (key: string, values?: Record<string, unknown>) => string, zone: string, language: string, thisYear: string): string {
  if (item.kind === 'calendar') return t('trash.calendarTitle', { name: item.title, count: item.events })
  const day = shortDay(eventDay(item, zone), language, thisYear)
  if (item.recurring) return t('trash.seriesTitle', { title: item.title, day: day.replace(/^\S+,?\s/, '') })
  return `${item.title} · ${day}`
}

export function TrashPage() {
  const { t, i18n } = useTranslation()
  // Without a connection nothing comes back and nothing goes (TIME-28 AC-3).
  const offline = useOffline()
  const { zone, twelve } = useDisplay()
  const language = i18n.language
  const { list: calendars } = useCalendars()
  useTitle(t('trash.title'))
  const [list, setList] = useState<TrashList | null>(null)
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState('')
  const [more, setMore] = useState(false)
  const [asking, setAsking] = useState<null | { kind: 'empty' } | { kind: 'target'; item: TrashEvent; calendarId: number | null }>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const now = useNow()
  const today = wallOf(now, zone).date
  const thisYear = today.slice(0, 4)

  /** The list anew; `keep` leaves the sentence of what just went wrong standing. */
  const load = useCallback(
    (keep = false) => {
      trashApi
        .list()
        .then((found) => {
          setList(found)
          if (!keep) setProblem('')
        })
        .catch(() => setProblem(t('trash.failed')))
    },
    [t],
  )
  useEffect(() => load(), [load])

  const loadMore = useCallback(() => {
    if (!list || more || list.items.length >= list.total) return
    setMore(true)
    trashApi
      .list(list.items.length)
      .then((found) =>
        setList((before) => {
          if (!before) return found
          const known = new Set(before.items.map((item) => `${item.kind}-${item.id}`))
          return { ...before, total: found.total, items: [...before.items, ...found.items.filter((item) => !known.has(`${item.kind}-${item.id}`))] }
        }),
      )
      .catch(() => undefined)
      .finally(() => setMore(false))
  }, [list, more])

  useEffect(() => {
    const end = sentinel.current
    if (!end || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore()
    }, { rootMargin: '400px' })
    observer.observe(end)
    return () => observer.disconnect()
  }, [loadMore])

  const restore = async (item: TrashItem, calendarId?: number) => {
    if (item.kind === 'event' && item.calendar_gone && calendarId === undefined) {
      // Its calendar is gone: where to (EC-1, EC-9).
      const first = calendars?.calendars.find((entry) => entry.writable && entry.id === calendars.default_id) ?? calendars?.calendars.find((entry) => entry.writable)
      setAsking({ kind: 'target', item, calendarId: first?.id ?? null })
      return
    }
    setAsking(null)
    setBusy(`${item.kind}-${item.id}`)
    try {
      await trashApi.restore(item, calendarId)
      showNotice({ text: t('trash.restored', { title: item.title }) })
      setList((before) => (before ? { ...before, total: before.total - 1, items: before.items.filter((entry) => !(entry.kind === item.kind && entry.id === item.id)) } : before))
      announce()
    } catch (error) {
      if (error instanceof ApiError && error.code === 'target_needed' && item.kind === 'event') {
        setBusy('')
        await restore({ ...item, calendar_gone: true })
        return
      }
      setProblem(problemText(error))
      // Restored or gone meanwhile in another window (EC-6): the list as it is now.
      if (error instanceof ApiError && error.code === 'not_in_trash') load(true)
    } finally {
      setBusy('')
    }
  }

  const empty = async () => {
    if (!list) return
    setAsking(null)
    setBusy('all')
    try {
      await trashApi.empty(list.now)
      showNotice({ text: t('trash.emptied') })
      load()
      announce()
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      setBusy('')
    }
  }

  /** "gelöscht von dir heute 14:02", "gelöscht von Anna am 20. Nov" (EC-14: in the member's zone). */
  const whoWhen = (item: TrashItem): string => {
    const wall = wallOf(Date.parse(item.deleted_at), zone)
    const time = clockText(wall.time, language, twelve)
    const when = wall.date === today ? t('trash.todayAt', { time }) : wall.date === addDays(today, -1) ? t('trash.yesterdayAt', { time }) : t('trash.onDay', { day: shortDay(wall.date, language, thisYear).replace(/^\S+,?\s/, '') })
    const said = !item.deleted_by ? t('trash.deletedWhen', { when }) : item.deleted_by.self ? t('trash.deletedBySelf', { when }) : t('trash.deletedBy', { name: item.deleted_by.name, when })
    // Through a key of a program (TIME-26 AC-10): the key named; gone by itself: why.
    const why = item.reason ? ` · ${t(`trash.reason.${item.reason}`)}` : ''
    return (item.deleted_via ? `${said} · ${t('trash.deletedVia', { key: item.deleted_via })}` : said) + why
  }

  const writable = calendars?.calendars.filter((entry) => entry.writable) ?? []

  return (
    <div className="flex min-h-full flex-col gap-4 px-4 py-5 md:px-8 md:py-6" data-testid="trash-page">
      <div className="flex max-w-[1000px] flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold text-mist-100">{t('trash.title')}</h1>
        {list && (
          <span className="text-[13px] text-mist-500" data-testid="trash-keeps">
            {t('trash.keeps', { count: list.trash_days })}
          </span>
        )}
        <button
          type="button"
          className="nt-btn ml-auto h-[38px] border border-bad-500 px-3.5 text-[13.5px] text-bad-500 hover:bg-ink-800"
          onClick={() => setAsking({ kind: 'empty' })}
          disabled={!list || list.total === 0 || busy !== '' || offline}
          data-testid="trash-empty"
        >
          {t('trash.empty')}
        </button>
      </div>
      {problem && (
        <p className="text-sm text-bad-500" role="alert" data-testid="trash-problem">
          {problem}
        </p>
      )}
      {list && list.total === 0 && (
        <div className="nt-empty max-w-[1000px] py-10" data-testid="trash-nothing">
          <Trash2 aria-hidden />
          <strong>{t('trash.nothing')}</strong>
          <span className="max-w-sm">{t('trash.nothingHint', { count: list.trash_days })}</span>
        </div>
      )}
      {list && list.items.length > 0 && (
        <ul className="flex max-w-[1000px] flex-col" aria-label={t('trash.title')}>
          {list.items.map((item) => {
            const title = titleLine(item, t, zone, language, thisYear)
            const second =
              item.kind === 'calendar'
                ? whoWhen(item)
                : item.kind === 'part' && item.series_gone
                  ? `${item.calendar.name} · ${t('trash.seriesGone')}`
                  : item.kind === 'event' && item.calendar_gone
                  ? `${item.calendar.name} · ${t('trash.calendarGone')}`
                  : `${item.calendar.name}${item.calendar.source ? ` (${t(`sources.kinds.${item.calendar.source}`)})` : ''} · ${whoWhen(item)}${item.exceptions ? t('trash.withExceptions') : ''}`
            return (
              <li key={`${item.kind}-${item.id}`} className="flex flex-wrap items-center gap-3.5 border-b border-ink-800 px-2.5 py-3" data-testid="trash-row" data-kind={item.kind} data-id={item.id}>
                <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[9px] bg-ink-800" aria-hidden data-color={item.color}>
                  {item.kind === 'calendar' ? <CalendarDays className="h-4 w-4" style={{ color: colorVar(item.color) }} /> : <Dot color={item.color} size={10} />}
                </span>
                <span className="flex min-w-0 flex-[1_1_300px] flex-col gap-0.5">
                  <span className="truncate text-[14.5px] font-semibold text-mist-100" title={title} data-testid="trash-title">
                    {title}
                  </span>
                  <span className={'nt-wrap text-[12.5px] ' + ((item.kind === 'event' && item.calendar_gone) || (item.kind === 'part' && item.series_gone) ? 'text-warn-500' : 'text-mist-500')} data-testid="trash-origin">
                    {second}
                  </span>
                </span>
                <span className={'flex-[0_0_130px] text-[12.5px] ' + (item.warn ? 'text-warn-500' : 'text-mist-500')} data-testid="trash-left" data-warn={item.warn || undefined}>
                  {item.days_left > 0 ? t('trash.left', { count: item.days_left }) : t('trash.lastDay')}
                </span>
                <button type="button" className="nt-btn nt-btn-ghost h-9 px-3 text-[13px]" onClick={() => void restore(item)} disabled={busy !== '' || offline} data-testid="trash-restore">
                  {t('trash.restore')}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <div ref={sentinel} />
      {list && list.items.length < list.total && (
        <div className="flex max-w-[1000px] justify-center">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={loadMore} disabled={more} data-testid="trash-more">
            {t('trash.more', { count: list.total - list.items.length })}
          </button>
        </div>
      )}
      <p className="max-w-[1000px] text-[12.5px] leading-relaxed text-mist-500" data-testid="trash-sources">
        {t('trash.sources')}
      </p>

      {asking?.kind === 'empty' && list && (
        <Dialog title={t('trash.confirmEmpty.title')} onClose={() => setAsking(null)}>
          <div className="flex flex-col gap-3.5" data-testid="trash-empty-dialog">
            <p className="text-sm leading-relaxed text-mist-300">{t('trash.confirmEmpty.text', { count: list.total })}</p>
            <p className="text-sm font-semibold text-bad-500">{t('trash.final')}</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setAsking(null)}>
                {t('common.cancel')}
              </button>
              <button type="button" className="nt-btn nt-btn-danger" onClick={() => void empty()} data-testid="trash-empty-go">
                {t('trash.confirmEmpty.go')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
      {asking?.kind === 'target' && (
        <Dialog title={t('trash.target.title', { title: asking.item.title })} onClose={() => setAsking(null)}>
          <div className="flex flex-col gap-3.5" data-testid="trash-target-dialog">
            <p className="text-sm leading-relaxed text-mist-300">{t('trash.target.text')}</p>
            <label className="flex flex-col gap-1.5 text-sm text-mist-300">
              {t('trash.target.calendar')}
              <select
                className="nt-field"
                value={asking.calendarId ?? ''}
                onChange={(event) => setAsking({ ...asking, calendarId: Number(event.target.value) })}
                data-testid="trash-target"
              >
                {writable.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setAsking(null)}>
                {t('common.cancel')}
              </button>
              <button
                type="button"
                className="nt-btn nt-btn-accent"
                disabled={asking.calendarId === null}
                onClick={() => asking.calendarId !== null && void restore(asking.item, asking.calendarId)}
                data-testid="trash-target-go"
              >
                {t('trash.restore')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}
