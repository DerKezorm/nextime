/* eslint-disable react-refresh/only-export-components */
/**
 * A deadline of nexpaper in a view: its kind in front ("Zahlen: …", "Kündigen bis: …"), a small mark of a document,
 * and on a click a popover with what nexpaper says of it and "In nexpaper öffnen" in a new tab. To look at only:
 * nothing is marked done or put off from here.
 */
import { ExternalLink, FileClock, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Menu } from '../../components/Menu'
import { useDisplay } from '../../lib/useDisplay'
import { daysBetween, format } from '../views/dates'
import { todayAt, useNow } from '../views/state'
import type { Deadline } from './api'

type Say = (key: string, values?: Record<string, unknown>) => string

/** "Zahlen: Rechnung Oktober" */
export function deadlineLine(deadline: Deadline, t: Say): string {
  return t('deadlines.line', { what: t(`deadlines.what.${deadline.what}`), title: deadline.title })
}

/** The amount with its currency in the page's language ("123,45 €"); "" without one. */
export function amountText(deadline: Deadline, language: string): string {
  if (deadline.amount === null || !deadline.currency) return ''
  try {
    return new Intl.NumberFormat(language, { style: 'currency', currency: deadline.currency }).format(deadline.amount / 100)
  } catch {
    return `${(deadline.amount / 100).toFixed(2)} ${deadline.currency}`
  }
}

/** "heute", or "überfällig seit Mo" (within a week) or "seit 12. Nov.", as the tasks of nextasks say it. */
export function dueText(deadline: Deadline, today: string, t: Say, language: string): string {
  if (deadline.day >= today) return t('deadlines.today')
  const day = daysBetween(deadline.day, today) < 7 ? format(deadline.day, language, { weekday: 'short' }).replace(/\.$/, '') : format(deadline.day, language, { day: 'numeric', month: 'short' })
  return t('deadlines.overdueSince', { day })
}

const isLink = (link: string) => /^https?:\/\//.test(link)

export function DeadlineMark({ small = false, late = false }: { small?: boolean; late?: boolean }) {
  return <FileClock className={(small ? 'h-3 w-3 ' : 'h-3.5 w-3.5 ') + 'shrink-0 ' + (late ? 'text-warn-500' : 'text-mist-400')} aria-hidden data-testid="deadline-mark" />
}

export function DeadlineTitle({ deadline, className = '' }: { deadline: Deadline; className?: string }) {
  const { t } = useTranslation()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const line = deadlineLine(deadline, t)
  return (
    <>
      <button
        type="button"
        className={'min-w-0 truncate text-left hover:underline ' + className}
        title={line}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        onClick={(event) => {
          event.stopPropagation()
          const target = event.currentTarget
          setAnchor((open) => (open ? null : target))
        }}
        data-testid="deadline-title"
      >
        {line}
      </button>
      {anchor && <DeadlinePopover deadline={deadline} anchor={anchor} onClose={() => setAnchor(null)} />}
    </>
  )
}

/** A click on a deadline: the day (overdue said so), what is to be done, title, sender, amount, the note of a
 * follow-up, and "In nexpaper öffnen". Escape and a click beside it close it. */
export function DeadlinePopover({ deadline, anchor, onClose }: { deadline: Deadline; anchor: HTMLElement; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const { zone } = useDisplay()
  const today = todayAt(useNow(), zone)
  const language = i18n.language
  const close = () => {
    onClose()
    anchor.focus()
  }
  const amount = amountText(deadline, language)
  return (
    <Menu anchor={anchor} onClose={onClose} label={deadline.title} align="start" width={300} className="p-3" testId="deadline-popover">
      <div className="flex items-start gap-2.5" onClick={(event) => event.stopPropagation()}>
        <span className="mt-1">
          <DeadlineMark late={deadline.day < today} />
        </span>
        <p className="nt-wrap min-w-0 flex-1 text-[14px] font-medium text-mist-100" data-testid="deadline-popover-title">
          {deadline.title}
        </p>
        <button type="button" className="nt-btn nt-btn-ghost -mt-1 -mr-1 h-7 w-7 shrink-0 px-0" aria-label={t('deadlines.close')} title={t('deadlines.close')} onClick={close} data-testid="deadline-popover-close">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <dl className="mt-2 space-y-1 text-[13px] text-mist-300" onClick={(event) => event.stopPropagation()}>
        <dd data-testid="deadline-popover-when">
          {t(`deadlines.what.${deadline.what}`)} · {format(deadline.day, language, { weekday: 'short', day: 'numeric', month: 'long', year: deadline.day.slice(0, 4) !== today.slice(0, 4) ? 'numeric' : undefined })}
          {deadline.day < today && <span className="text-warn-500"> · {dueText(deadline, today, t, language)}</span>}
        </dd>
        {deadline.sender && <dd data-testid="deadline-popover-sender">{t('deadlines.from', { sender: deadline.sender })}</dd>}
        {amount && (
          <dd className="font-medium text-mist-100" data-testid="deadline-popover-amount">
            {amount}
          </dd>
        )}
        {deadline.note && (
          <dd className="line-clamp-3 whitespace-pre-line text-mist-500" data-testid="deadline-popover-note">
            {deadline.note}
          </dd>
        )}
        {deadline.status === 'inbox' && <dd className="text-mist-500">{t('deadlines.inbox')}</dd>}
      </dl>
      {isLink(deadline.link) && (
        <a
          role="menuitem"
          href={deadline.link}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 flex items-center gap-1.5 text-[13px] text-accent-400 hover:underline"
          onClick={(event) => {
            event.stopPropagation()
            onClose()
          }}
          data-testid="deadline-popover-open"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          {t('deadlines.openInNexpaper')}
        </a>
      )}
    </Menu>
  )
}

/** A deadline as a row of a list: mark, text, and on the right when it is due. */
export function DeadlineRow({ deadline, when, late = false }: { deadline: Deadline; when?: string; late?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5 text-[14px]" data-testid="deadline-row" data-deadline={deadline.id}>
      <span className="nt-check-hit">
        <DeadlineMark late={late} />
      </span>
      <DeadlineTitle deadline={deadline} className="text-mist-100" />
      {when && (
        <span className={'ml-auto shrink-0 text-xs whitespace-nowrap ' + (late ? 'text-warn-500' : 'text-mist-500')} data-testid="deadline-when">
          {when}
        </span>
      )}
    </div>
  )
}

/** A deadline in the all-day row of Day and Week: small, dashed like a task. */
export function DeadlineChip({ deadline }: { deadline: Deadline }) {
  return (
    <div className="nt-task-chip" data-testid="deadline-chip" data-deadline={deadline.id}>
      <DeadlineMark small />
      <DeadlineTitle deadline={deadline} className="text-[11px] text-mist-100" />
    </div>
  )
}

/** A deadline in a day of the month, after the day's events and tasks. */
export function MonthDeadline({ deadline }: { deadline: Deadline }) {
  return (
    <div className="nt-month-event text-mist-400" data-deadline={deadline.id} data-testid="month-deadline">
      <DeadlineMark small />
      <DeadlineTitle deadline={deadline} className="text-mist-400" />
    </div>
  )
}
