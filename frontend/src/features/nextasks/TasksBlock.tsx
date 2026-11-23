/**
 * The block "Aufgaben aus nextasks" under the events of today (TIME-25 AC-4, AC-6): each task due today or overdue
 * with its box, "heute" or "überfällig seit Mo"; the first five and "+n weitere" (EC-8). nextasks away: a quiet line
 * instead of the tasks (EC-1). A tick nextasks refused for good says so once (EC-3).
 */
import { X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { daysBetween, format } from '../views/dates'
import { nextasksApi, type Task, type TaskNotice } from './api'
import { TaskNote, TaskRow } from './TaskItem'

/** Tasks shown before "+n weitere" (EC-8). */
export const FIRST_TASKS = 5

/** `phone`: at phone width the tasks stand in the list of today with the word "Aufgabe" (TIME-11 AC-10), without a
 * card and heading of their own. */
export function TasksBlock({ tasks, today, problem, notices, onSeen, phone = false }: { tasks: Task[]; today: string; problem: string | null; notices: TaskNotice[]; onSeen: () => void; phone?: boolean }) {
  const { t, i18n } = useTranslation()
  const [all, setAll] = useState(false)
  if (!tasks.length && !problem && !notices.length) return null
  const shown = all ? tasks : tasks.slice(0, FIRST_TASKS)
  const when = (task: Task) => (phone ? `${t('tasks.kind')} · ${due(task)}` : due(task))
  const due = (task: Task) => {
    if (task.day >= today) return t('tasks.today')
    // Within the last week the weekday says it ("seit Mo"), older the date ("seit 12. Nov.").
    const day = daysBetween(task.day, today) < 7 ? format(task.day, i18n.language, { weekday: 'short' }).replace(/\.$/, '') : format(task.day, i18n.language, { day: 'numeric', month: 'short' })
    return t('tasks.overdueSince', { day })
  }
  const seen = async (notice: TaskNotice) => {
    await nextasksApi.seen(notice.id).catch(() => undefined)
    onSeen()
  }
  return (
    <section
      className={'flex flex-col gap-2.5 ' + (phone ? 'px-1' : 'rounded-[12px] border border-ink-700 bg-ink-850 px-4 py-3')}
      aria-labelledby="tasks-block"
      data-testid="tasks-block"
      data-phone={phone || undefined}
    >
      <h2 id="tasks-block" className={phone ? 'sr-only' : 'text-xs font-semibold tracking-wider text-mist-500 uppercase'}>
        {t('tasks.block')}
      </h2>
      {problem && (
        <p className="text-xs text-mist-500" data-testid="tasks-problem">
          {problem === 'nextasks_unreachable' || problem === 'nextasks_slow_down' || problem === 'nextasks_busy' ? t('tasks.unreachable') : t('tasks.problem')}
        </p>
      )}
      {notices.map((notice) => (
        <p key={notice.id} role="status" className="flex items-start gap-2 text-xs text-warn-500" data-testid="tasks-notice">
          <span className="min-w-0 flex-1">{t('tasks.notArrived', { title: notice.title })}</span>
          <button type="button" className="shrink-0 rounded p-0.5 text-mist-500 hover:text-mist-100" aria-label={t('tasks.close')} onClick={() => void seen(notice)} data-testid="tasks-notice-close">
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </p>
      ))}
      {shown.map((task) => (
        <TaskRow key={task.id} task={task} when={when(task)} late={task.day < today} />
      ))}
      {tasks.length > FIRST_TASKS && (
        <button type="button" className="nt-btn-quiet self-start text-xs font-semibold" aria-expanded={all} onClick={() => setAll(!all)} data-testid="tasks-more">
          {all ? t('tasks.less') : t('tasks.more', { count: tasks.length - FIRST_TASKS })}
        </button>
      )}
      <TaskNote />
    </section>
  )
}
