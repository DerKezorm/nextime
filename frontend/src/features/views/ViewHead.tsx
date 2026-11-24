/**
 * The head of Day, Week and Month (TIME-12 AC-1, AC-13): ‹ and › step a day, a week (three days on a phone) or a
 * month, "Today" jumps back, the period in words as a button that opens the choice of a date (`DatePicker`, key G), the
 * calendar week as a button ("KW 48", or "Zu KW …" while week numbers are off) that opens "Zu Kalenderwoche springen"
 * (with a date field too), and the switch Day, Week, Month, Year (TIME-12 AC-16; the year steps a year and has no week
 * button).
 */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Dialog } from '../../components/Dialog'
import { isoWeekOf, mondayOfWeek, weeksInYear } from './dates'
import { DatePicker } from './DatePicker'

export type Kind = 'day' | 'week' | 'month' | 'year'

const PATHS: Record<Kind, string> = { day: '/day', week: '/week', month: '/month', year: '/year' }
const KEYS: Record<Kind, string> = { day: 'T', week: 'W', month: 'M', year: 'J' }

export function ViewHead({ kind, title, week, narrow, onStep, onToday, onJump, day, today, picking, onPicking, onPick }: {
  kind: Kind
  title: string
  /** The day shown, and today: the choice of a date opens at the one and marks the other. */
  day: string
  today: string
  /** The choice of a date is open (the title or the key G opened it). */
  picking: boolean
  onPicking: (open: boolean) => void
  onPick: (date: string) => void
  /** The week the button names (the one shown, or the first of the month's). */
  week: number | null
  narrow: boolean
  onStep: (step: -1 | 1) => void
  onToday: () => void
  onJump: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const steps = kind === 'day' ? ['views.previousDay', 'views.nextDay'] : kind === 'month' ? ['views.previousMonth', 'views.nextMonth'] : kind === 'year' ? ['views.previousYear', 'views.nextYear'] : narrow ? ['views.previousThree', 'views.nextThree'] : ['views.previousWeek', 'views.nextWeek']
  const names: Record<Kind, string> = { day: t('views.day'), week: t('nav.week'), month: t('nav.month'), year: t('views.year') }
  const titleButton = useRef<HTMLButtonElement>(null)
  return (
    <div className="flex flex-wrap items-center gap-2 sm:gap-3" data-testid="view-head">
      <button type="button" className="nt-btn nt-btn-ghost w-10 px-0" aria-label={t(steps[0])} onClick={() => onStep(-1)} data-testid="step-back">
        ‹
      </button>
      <button type="button" className="nt-btn nt-btn-ghost w-10 px-0" aria-label={t(steps[1])} onClick={() => onStep(1)} data-testid="step-on">
        ›
      </button>
      {!narrow && (
        <button type="button" className="nt-btn nt-btn-ghost" onClick={onToday} data-testid="to-today">
          {t('nav.today')}
        </button>
      )}
      <h1 className="min-w-0 text-lg font-bold tracking-tight text-mist-100 sm:text-xl">
        <button
          ref={titleButton}
          type="button"
          className={'rounded-[8px] px-1 text-left hover:bg-ink-800 ' + (picking ? 'bg-[var(--color-accent-wash)]' : '')}
          title={t('views.pickHint')}
          aria-haspopup="dialog"
          aria-expanded={picking}
          onClick={() => onPicking(!picking)}
          data-testid="period"
        >
          {title}
        </button>
      </h1>
      {picking && titleButton.current && (
        <DatePicker
          anchor={titleButton.current}
          value={day}
          today={today}
          onClose={() => onPicking(false)}
          onPick={(date) => {
            onPicking(false)
            onPick(date)
          }}
        />
      )}
      {kind !== 'year' && (
        <button type="button" className="text-sm font-medium text-mist-500 hover:text-accent-400" title={t('views.jumpHint')} aria-label={t('views.jumpTitle')} onClick={onJump} data-testid="week-button">
          <span data-testid={week !== null ? 'week-number' : undefined}>{week !== null ? t('views.weekNumber', { number: week }) : t('views.toWeek')}</span>
        </button>
      )}
      {narrow ? (
        <span className="ml-auto inline-flex items-center gap-1">
          {/* On a phone the bar below has no room for the year: the month leads there, and back by a month's name. */}
          {kind === 'month' && (
            <button type="button" className="nt-btn nt-btn-quiet text-sm" onClick={() => navigate(PATHS.year)} data-testid="to-year">
              {t('views.year')}
            </button>
          )}
          <button type="button" className="nt-btn nt-btn-quiet text-sm" onClick={onToday} data-testid="to-today">
            {t('nav.today')}
          </button>
        </span>
      ) : (
        <span role="group" aria-label={t('app.views')} className="ml-auto inline-flex overflow-hidden rounded-[9px] border border-ink-700 text-sm" data-testid="view-switch">
          {(['day', 'week', 'month', 'year'] as Kind[]).map((entry) => (
            <button
              key={entry}
              type="button"
              aria-pressed={entry === kind}
              title={t('views.keyHint', { name: names[entry], key: KEYS[entry] })}
              className={'px-3.5 py-1.5 ' + (entry === kind ? 'nt-chosen font-semibold' : 'text-mist-500 hover:text-mist-100')}
              onClick={() => navigate(PATHS[entry])}
            >
              {names[entry]}
            </button>
          ))}
        </span>
      )}
    </div>
  )
}

/**
 * "Zu Kalenderwoche springen" (TIME-12 AC-13, AC-15, EC-15 to EC-17): week and year, filled in with the week shown;
 * Enter or "Springen" gives the Monday of that ISO week. A week the year does not have, or no number, says so and
 * stays.
 */
export function WeekJump({ from, today, onJump, onDate, onClose }: { from: string; today: string; onJump: (monday: string) => void; onDate: (date: string) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const shown = isoWeekOf(from)
  const [week, setWeek] = useState(String(shown.week))
  const [year, setYear] = useState(String(shown.year))
  const [problem, setProblem] = useState<string | null>(null)
  // "Oder zu einem Datum": a date filled in wins over the week.
  const [date, setDate] = useState('')
  const jump = () => {
    if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      onDate(date)
      return
    }
    if (!/^\d{4}$/.test(year.trim())) {
      setProblem(t('views.jumpYearWrong'))
      return
    }
    const chosenYear = Number(year.trim())
    const max = weeksInYear(chosenYear)
    const number = /^\d{1,2}$/.test(week.trim()) ? Number(week.trim()) : 0
    if (number < 1 || number > max) {
      setProblem(t('views.jumpWrong', { year: chosenYear, max }))
      return
    }
    onJump(mondayOfWeek(number, chosenYear))
  }
  return (
    <Dialog title={t('views.jumpTitle')} onClose={onClose}>
      <form
        className="space-y-3"
        data-testid="week-jump"
        onSubmit={(event) => {
          event.preventDefault()
          jump()
        }}
      >
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="font-medium text-mist-100">{t('views.jumpWeek')}</span>
            <input className={'nt-field mt-1 ' + (problem ? 'border-bad-500' : '')} inputMode="numeric" value={week} aria-invalid={problem ? true : undefined} onChange={(event) => setWeek(event.target.value)} data-testid="jump-week" />
          </label>
          <label className="block text-sm">
            <span className="font-medium text-mist-100">{t('views.jumpYear')}</span>
            <input className="nt-field mt-1" inputMode="numeric" value={year} onChange={(event) => setYear(event.target.value)} data-testid="jump-year" />
          </label>
        </div>
        <label className="block text-sm">
          <span className="font-medium text-mist-100">{t('views.jumpDate')}</span>
          <input type="date" className="nt-field nt-date mt-1" value={date} onChange={(event) => setDate(event.target.value)} data-testid="jump-date" />
        </label>
        {problem ? (
          <p role="alert" className="text-sm text-bad-500" data-testid="jump-problem">
            {problem}
          </p>
        ) : (
          <p className="text-xs text-mist-500">{t('views.jumpNow', { number: isoWeekOf(today).week })}</p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" data-testid="jump-go">
            {t('views.jumpGo')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
