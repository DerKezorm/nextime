/**
 * The jump to a date (TIME-12, asked for while testing): a small month as a
 * popover beside the title of the head, built like the app's menu (`Menu.tsx`: a portal, beside its button, upward
 * when there is no room, Escape and a click outside close it, the focus goes back). ‹ › change the month, month and
 * year are chosen directly, "Heute" shows today's month; the week begins on the day the account chose, with calendar
 * weeks when they are on, the weekend tinted as in the views. A day is one stop in the tab order; the arrow keys move a
 * day or a week (into the next month too), Page Up and Down a month, Enter or a click takes it.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { menuPlace } from '../../components/Menu'
import { useDisplay } from '../../lib/useDisplay'
import { addDays, addMonths, format, isoWeekOf, isWeekend, monthWeeks, weekday } from './dates'
import { swallowClick } from '../../lib/swallow'

/** Years the year choice offers on each side of the one shown. */
const YEARS_AROUND = 15

export function DatePicker({ anchor, value, today, onPick, onClose }: {
  anchor: HTMLElement
  /** The day the view shows: its month opens, the day is marked. */
  value: string
  today: string
  onPick: (date: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { display, language } = useDisplay()
  const [month, setMonth] = useState(value.slice(0, 7))
  const [focused, setFocused] = useState(value)
  const panel = useRef<HTMLDivElement>(null)
  const grid = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  }, [onClose])

  const measure = useCallback(() => {
    const element = panel.current
    if (!element) return
    setPlace(menuPlace(anchor.getBoundingClientRect(), { width: element.offsetWidth, height: element.offsetHeight }, { width: window.innerWidth, height: window.innerHeight }, 'start'))
  }, [anchor])
  useLayoutEffect(measure, [measure, month])

  // The chosen day takes the focus once the popover stands where it belongs.
  const placed = place !== null
  useEffect(() => {
    if (placed) grid.current?.querySelector<HTMLElement>('[tabindex="0"]')?.focus()
  }, [placed])

  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const target = event.target as Node
      if (panel.current?.contains(target) || anchor.contains(target)) return
      close.current()
      swallowClick(event)
    }
    window.addEventListener('pointerdown', outside, true)
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [anchor, measure])

  const weeks = useMemo(() => monthWeeks(`${month}-01`, display.week_start), [month, display.week_start])
  const heads = weeks[0].map((date) => format(date, language, { weekday: 'narrow' }))
  const year = Number(month.slice(0, 4))
  const years = Array.from({ length: YEARS_AROUND * 2 + 1 }, (_, index) => year - YEARS_AROUND + index)
  const months = Array.from({ length: 12 }, (_, index) => format(`2026-${String(index + 1).padStart(2, '0')}-01`, language, { month: 'long' }))

  const leave = () => {
    close.current()
    anchor.focus()
  }
  const goTo = (date: string, focus = true) => {
    setFocused(date)
    setMonth(date.slice(0, 7))
    if (focus) requestAnimationFrame(() => grid.current?.querySelector<HTMLElement>(`[data-date="${date}"]`)?.focus())
  }
  const showMonth = (next: string) => {
    setMonth(next)
    // The focus stop moves along: the same day of the month where there is one.
    const day = Math.min(Number(focused.slice(8, 10)), 28)
    setFocused(`${next}-${String(day).padStart(2, '0')}`)
  }

  /** A day taken: the popover closes and the focus goes back to the title it was opened from (review, 2026-11-23). */
  const take = (date: string) => {
    onPick(date)
    anchor.focus()
  }

  const keys = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      leave()
      return
    }
    if (event.key === 'Tab') {
      // Tab stays inside the popover, as in a dialog; Escape leaves it (review of the additions, 2026-11-23).
      const inside = [...(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), select, [tabindex="0"]') ?? [])].filter((element) => element.tabIndex >= 0)
      const first = inside[0]
      const last = inside[inside.length - 1]
      if (first && last && event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (first && last && !event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
      return
    }
    const onDay = (event.target as HTMLElement).dataset?.date
    if (!onDay) return
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key]
    if (step) goTo(addDays(onDay, step))
    else if (event.key === 'PageUp' || event.key === 'PageDown') {
      const target = addMonths(onDay, event.key === 'PageUp' ? -1 : 1)
      goTo(`${target.slice(0, 7)}-${String(Math.min(Number(onDay.slice(8, 10)), 28)).padStart(2, '0')}`)
    } else return
    event.preventDefault()
    event.stopPropagation()
  }

  return createPortal(
    <div
      ref={panel}
      role="dialog"
      aria-label={t('views.pickTitle')}
      onKeyDown={keys}
      className="nt-menu fixed z-[60] w-[19rem] max-w-[calc(100vw-16px)] p-3"
      style={{ left: place?.left ?? 0, top: place?.top ?? 0, visibility: place ? 'visible' : 'hidden' }}
      data-testid="date-picker"
    >
      <div className="mb-2 flex items-center gap-1">
        <button type="button" className="nt-btn nt-btn-ghost h-8 w-8 px-0" aria-label={t('views.previousMonth')} onClick={() => showMonth(addMonths(`${month}-01`, -1).slice(0, 7))} data-testid="pick-back">
          ‹
        </button>
        <select className="nt-field h-8 min-w-0 flex-1 px-2 py-0 text-sm" aria-label={t('views.pickMonth')} value={Number(month.slice(5, 7))} onChange={(event) => showMonth(`${month.slice(0, 4)}-${event.target.value.padStart(2, '0')}`)} data-testid="pick-month">
          {months.map((name, index) => (
            <option key={name} value={index + 1}>
              {name}
            </option>
          ))}
        </select>
        <select className="nt-field h-8 w-[5.5rem] px-2 py-0 text-sm" aria-label={t('views.pickYear')} value={year} onChange={(event) => showMonth(`${event.target.value}-${month.slice(5, 7)}`)} data-testid="pick-year">
          {years.map((entry) => (
            <option key={entry} value={entry}>
              {entry}
            </option>
          ))}
        </select>
        <button type="button" className="nt-btn nt-btn-ghost h-8 w-8 px-0" aria-label={t('views.nextMonth')} onClick={() => showMonth(addMonths(`${month}-01`, 1).slice(0, 7))} data-testid="pick-on">
          ›
        </button>
      </div>
      <div ref={grid} role="grid" aria-label={format(`${month}-01`, language, { month: 'long', year: 'numeric' })} className="text-center text-[13px]" data-testid="pick-grid">
        <div role="row" className={'grid gap-0.5 ' + (display.week_numbers ? 'grid-cols-8' : 'grid-cols-7')}>
          {display.week_numbers && (
            <span role="columnheader" className="py-1 text-[11px] font-semibold text-mist-500">
              {t('views.weekShort')}
            </span>
          )}
          {heads.map((name, index) => (
            <span key={index} role="columnheader" className="py-1 text-[11px] font-semibold text-mist-500">
              {name}
            </span>
          ))}
        </div>
        {weeks.map((week) => (
          <div key={week[0]} role="row" className={'grid gap-0.5 ' + (display.week_numbers ? 'grid-cols-8' : 'grid-cols-7')}>
            {display.week_numbers && (
              <span role="rowheader" className="flex items-center justify-center text-[11px] text-mist-500" data-testid="pick-week">
                {isoWeekOf(week.find((date) => weekday(date) === 4) ?? week[0]).week}
              </span>
            )}
            {week.map((date) => {
              const inMonth = date.startsWith(month)
              const chosen = date === value
              const isToday = date === today
              return (
                <button
                  key={date}
                  type="button"
                  role="gridcell"
                  tabIndex={date === focused ? 0 : -1}
                  aria-selected={chosen}
                  aria-current={isToday ? 'date' : undefined}
                  aria-label={format(date, language, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
                  data-date={date}
                  data-weekend={isWeekend(date) || undefined}
                  onClick={() => take(date)}
                  className={
                    'h-8 rounded-[8px] outline-offset-1 ' +
                    (chosen ? 'bg-accent-500 font-semibold text-on-accent ' : isToday ? 'font-bold text-accent-400 ring-1 ring-accent-500 ' : inMonth ? 'text-mist-100 hover:bg-ink-800 ' : 'text-mist-500 hover:bg-ink-800 ') +
                    (isWeekend(date) && !chosen ? 'nt-weekend ' : '')
                  }
                >
                  {Number(date.slice(8, 10))}
                </button>
              )
            })}
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-end">
        <button type="button" className="nt-btn nt-btn-quiet text-sm" onClick={() => take(today)} data-testid="pick-today">
          {t('nav.today')}
        </button>
      </div>
    </div>,
    document.body,
  )
}
