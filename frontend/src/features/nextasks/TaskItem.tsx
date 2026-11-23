/* eslint-disable react-refresh/only-export-components */
/**
 * A task of nextasks in a view (TIME-25): a box to tick, apart from the title that opens the task in nextasks (EC-15).
 * Ticked it stays struck through until the page reloads; a second tap takes it back, also in nextasks (AC-9). A double
 * click is one tap (EC-10). nextasks away: the tick counts here at once and carries "wartet" (EC-3). A key that only
 * reads leaves the task open and says why (EC-4). A task gone in nextasks says so on opening and leaves (EC-7). Without
 * a connection nothing is ticked (TIME-28).
 */
import { Check, ExternalLink, Repeat, X } from 'lucide-react'
import { useState, useSyncExternalStore, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { Menu } from '../../components/Menu'
import { problemText } from '../../lib/errors'
import { useDisplay } from '../../lib/useDisplay'
import { newRef } from '../calendars/api'
import { clockText } from '../calendars/time'
import { useOffline } from '../offline/connection'
import { format, shortRule } from '../views/dates'
import { AreaMark } from './AreaMark'
import { colorOfTask } from './look'
import { todayAt, useNow } from '../views/state'
import { forgetTick, nextasksApi, noteGone, noteTick, taps, tickedTasks, type Task, type TickAnswer } from './api'

/** Two taps on the same box within this time are one (a double click, EC-10). */
const DOUBLE_MS = 600


// ---- The quiet line that says what happened ---------------------------------------------------------------------------

let note = ''
const noteListeners = new Set<() => void>()
let noteTimer: number | undefined

export function setTaskNote(text: string): void {
  note = text
  for (const listener of [...noteListeners]) listener()
  window.clearTimeout(noteTimer)
  if (text) noteTimer = window.setTimeout(() => setTaskNote(''), 8000)
}

export function useTaskNote(): string {
  return useSyncExternalStore(
    (listener) => {
      noteListeners.add(listener)
      return () => noteListeners.delete(listener)
    },
    () => note,
  )
}

export function TaskNote() {
  const text = useTaskNote()
  if (!text) return null
  return (
    <p role="status" className="text-xs text-mist-500" data-testid="task-note">
      {text}
    </p>
  )
}

// ---- Ticking and opening ----------------------------------------------------------------------------------------------

type Say = (key: string, values?: Record<string, unknown>) => string

export async function tickTask(task: Task, t: Say): Promise<TickAnswer | null> {
  const now = Date.now()
  if (taps.running.has(task.id) || now - (taps.last.get(task.id) ?? 0) < DOUBLE_MS) return null
  taps.last.set(task.id, now)
  taps.running.add(task.id)
  const before = tickedTasks().get(task.id)
  const done = !task.done
  noteTick(task, done, false)
  try {
    const answer = await nextasksApi.tick(task.id, done, newRef(), task.title)
    if (answer.state === 'waiting') noteTick(task, done, true)
    else if (answer.state === 'failed') {
      noteTick(task, !done, false)
      setTaskNote(t('tasks.notArrived', { title: task.title }))
    }
    return answer
  } catch (error) {
    // Refused before anything went: the task stays as it was, with the reason (EC-4).
    if (before) noteTick(before.task, before.done, before.waiting)
    else forgetTick(task.id)
    setTaskNote(error instanceof ApiError && error.code === 'nextasks_read_only' ? t('tasks.readOnly') : problemText(error))
    return null
  } finally {
    taps.running.delete(task.id)
  }
}

export async function openTask(task: Task, t: Say): Promise<void> {
  // A window opened at the click itself, so no blocker stops it; it is closed again when the task is gone.
  const opened = window.open('', '_blank')
  if (opened) opened.opener = null
  try {
    const answer = await nextasksApi.open(task.id)
    if (answer.gone) {
      opened?.close()
      noteGone(task.id)
      setTaskNote(t('tasks.gone'))
    } else if (answer.link && /^https?:\/\//.test(answer.link)) {
      if (opened) opened.location.href = answer.link
    } else opened?.close()
  } catch (error) {
    opened?.close()
    setTaskNote(problemText(error))
  }
}

// ---- The pieces ---------------------------------------------------------------------------------------------------------

export function TaskBox({ task, small = false }: { task: Task; small?: boolean }) {
  const { t } = useTranslation()
  const offline = useOffline()
  // A coming day of a repeating task is to look at: its mark instead of a box (only the one due now ticks).
  if (task.preview)
    return (
      <span className={small ? 'inline-flex shrink-0' : 'nt-check-hit'} title={t('tasks.previewTitle')} data-testid="task-preview-mark">
        <Repeat className={small ? 'h-3 w-3 text-mist-500' : 'h-3.5 w-3.5 text-mist-500'} aria-hidden />
      </span>
    )
  return (
    <span className={small ? 'inline-flex shrink-0' : 'nt-check-hit'}>
      <button
        type="button"
        role="checkbox"
        aria-checked={task.done}
        aria-label={task.done ? t('tasks.untick', { title: task.title }) : t('tasks.tick', { title: task.title })}
        title={offline ? t('tasks.offline') : undefined}
        disabled={offline}
        className="nt-check"
        // The box in the colour of its list, when nextasks gives one.
        style={{ ...(small ? { width: 14, height: 14 } : {}), ...(colorOfTask(task.color) ? { borderColor: colorOfTask(task.color)! } : {}) } as CSSProperties}
        onClick={(event) => {
          event.stopPropagation()
          void tickTask(task, t)
        }}
        data-testid="task-tick"
      >
        {task.done && <Check className="h-3 w-3" strokeWidth={3.5} aria-hidden />}
      </button>
    </span>
  )
}

export function TaskTitle({ task, className = '' }: { task: Task; className?: string }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  return (
    <>
      <button
        type="button"
        className={'min-w-0 truncate text-left hover:underline ' + (task.done ? 'nt-done ' : '') + (task.preview ? 'text-mist-500 ' : '') + className}
        title={task.title}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        onClick={(event) => {
          event.stopPropagation()
          const target = event.currentTarget
          setAnchor((open) => (open ? null : target))
        }}
        data-testid="task-title"
      >
        {task.title}
      </button>
      {anchor && <TaskPopover task={task} anchor={anchor} onClose={() => setAnchor(null)} />}
    </>
  )
}

/** A click on a task (TIME-25 popover, asked for while testing, 2026-11-23): a small popover at the entry with what
 * nextasks says of it and "In nextasks öffnen" in a new tab. Escape and a click beside it close it. */
export function TaskPopover({ task, anchor, onClose }: { task: Task; anchor: HTMLElement; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const offline = useOffline()
  const [movedOn, setMovedOn] = useState(false)
  const close = () => {
    onClose()
    anchor.focus()
  }
  const { zone, twelve } = useDisplay()
  const today = todayAt(useNow(), zone)
  const language = i18n.language
  const day = format(task.day, language, { weekday: 'short', day: 'numeric', month: 'long' })
  const when = task.time ? `${day}, ${clockText(task.time, language, twelve)}` : day
  const rule = task.repeat ? shortRule(task.repeat, t) : ''
  const place = [task.area_name, task.list_name, task.section].filter(Boolean).join(' › ')
  return (
    <Menu anchor={anchor} onClose={onClose} label={task.title} align="start" width={300} className="p-3" testId="task-popover">
      <div className="flex items-start gap-2.5" onClick={(event) => event.stopPropagation()}>
        <span className="mt-0.5">
          <TaskBox task={task} />
        </span>
        <p className={'nt-wrap min-w-0 flex-1 text-[14px] font-medium text-mist-100 ' + (task.done ? 'nt-done' : '')} data-testid="task-popover-title">
          {task.title}
        </p>
        <button type="button" className="nt-btn nt-btn-ghost -mt-1 -mr-1 h-7 w-7 shrink-0 px-0" aria-label={t('tasks.close')} title={t('tasks.close')} onClick={close} data-testid="task-popover-close">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      <dl className="mt-2 space-y-1 text-[13px] text-mist-300" onClick={(event) => event.stopPropagation()}>
        {place && (
          <dd className="flex items-center gap-1.5" data-testid="task-popover-place">
            <AreaMark area={task} size={12} />
            {place}
          </dd>
        )}
        <dd data-testid="task-popover-when">
          {task.preview ? t('tasks.previewOn', { day: when }) : when}
          {!task.preview && task.day < today && <span className="text-warn-500"> · {t('tasks.overdueSince', { day: format(task.day, language, { weekday: 'short' }) })}</span>}
        </dd>
        {rule && <dd data-testid="task-popover-rule">{t('tasks.repeats', { rule })}</dd>}
        {task.assignees?.length ? <dd data-testid="task-popover-assigned">{t('tasks.assignedTo', { names: task.assignees.join(', ') })}</dd> : null}
        {task.notes && (
          <dd className="line-clamp-3 whitespace-pre-line text-mist-500" data-testid="task-popover-notes">
            {task.notes}
          </dd>
        )}
      </dl>
      <div className="mt-3 flex items-center gap-2">
      {task.link && (
        <a
          role="menuitem"
          href={task.link}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-[13px] text-accent-400 hover:underline"
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            onClose()
            void openTask(task, t)
          }}
          data-testid="task-popover-open"
        >
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          {t('tasks.openInNextasks')}
        </a>
      )}
      {!offline && !task.preview && (
        <button
          type="button"
          role="menuitem"
          className="nt-btn nt-btn-ghost ml-auto h-8 text-[13px]"
          onClick={(event) => {
            event.stopPropagation()
            void tickTask(task, t).then((answer) => setMovedOn(Boolean(answer?.moved_on)))
          }}
          data-testid="task-popover-done"
        >
          {task.done ? t('tasks.markOpen') : t('tasks.markDone')}
        </button>
      )}
      </div>
      {movedOn && (
        <p role="status" className="mt-2 text-xs text-mist-500" data-testid="task-popover-moved-on">
          {t('tasks.movedOn')}
        </p>
      )}
    </Menu>
  )
}

export function Waiting({ task }: { task: Task }) {
  const { t } = useTranslation()
  if (!task.waiting) return null
  return (
    <span className="shrink-0 text-[11px] text-mist-500" data-testid="task-waiting">
      {t('tasks.waiting')}
    </span>
  )
}

/** A task as a row of a list: box, title, "wartet", and on the right when it is due. */
export function TaskRow({ task, when, late = false }: { task: Task; when?: string; late?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5 text-[14px]" data-testid="task-row" data-task={task.id}>
      <TaskBox task={task} />
      <TaskTitle task={task} className={task.done ? '' : 'text-mist-100'} />
      <Waiting task={task} />
      {when && (
        <span className={'ml-auto shrink-0 text-xs whitespace-nowrap ' + (late ? 'text-warn-500' : 'text-mist-500')} data-testid="task-when">
          {when}
        </span>
      )}
    </div>
  )
}

/** A task in the all-day row or at its time in Day and Week: small, dashed. */
export function TaskChip({ task, at }: { task: Task; at?: number }) {
  return (
    <div
      className={'nt-task-chip ' + (at !== undefined ? 'nt-task-timed' : '')}
      style={at !== undefined ? ({ '--s': at } as CSSProperties) : undefined}
      data-testid="task-chip"
      data-task={task.id}
    >
      <TaskBox task={task} small />
      <TaskTitle task={task} className={'text-[11px] ' + (task.done ? '' : 'text-mist-100')} />
      <Waiting task={task} />
    </div>
  )
}
