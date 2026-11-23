/**
 * nextasks for any part of the page (TIME-25): the connection, whether tasks show at all (AC-5, AC-7), and the tasks
 * due up to a day, asked when a view opens and every minute (EC-2), with what was ticked here laid over them (AC-9).
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'

import { useShown } from '../collections/store'
import { isOffline } from '../offline/connection'
import { choiceRound, goneTasks, loadNextasks, nextasksApi, nextasksStatus, onNextasks, setTaskAreas, taskAreas, taskRevision, tickedTasks, type NextasksStatus, type Task, type TaskArea, type TaskList, type TaskNotice } from './api'

/** How often a view asks again (EC-2); the server keeps nextasks' answer a minute as well. */
export const TASKS_EVERY_MS = 60_000

export function useNextasks(): NextasksStatus | null {
  const status = useSyncExternalStore(onNextasks, nextasksStatus)
  useEffect(() => {
    if (!nextasksStatus() && !isOffline()) void loadNextasks().catch(() => undefined)
  }, [])
  return status
}

/** The areas of nextasks chosen in "Verbindungen", each with its eye (TIME-25 area rows). */
export function useTaskAreas(): TaskArea[] {
  return useSyncExternalStore(onNextasks, taskAreas)
}

/** Which tasks show (AC-7, area rows): connected, not one calendar alone; an area alone shows its own only; else the
 * areas not hidden by their eye and, in a collection, those in it. `null`: none at all. */
export function useTaskFilter(): ((task: Task) => boolean) | null {
  const status = useNextasks()
  const shown = useShown()
  const areas = useTaskAreas()
  if (!status?.connected || shown.only) return null
  // A nextasks without areas: the one row "Meine Aufgaben" with its eye.
  if (!areas.length && status.hidden) return null
  const alone = shown.onlyArea
  if (alone) return (task) => task.row === alone.id
  const hidden = new Set(areas.filter((area) => area.hidden).map((area) => area.id))
  const inside = shown.collection ? (shown.collection.task_areas ?? (shown.collection.with_tasks ? ['*'] : [])) : ['*']
  if (!inside.length) return null
  const every = inside.includes('*')
  return (task) => !hidden.has(task.row ?? '') && (every || inside.includes(task.row ?? ''))
}

/** Whether any task may show at all (the views ask nextasks only then). */
export function useTasksShown(): boolean {
  return useTaskFilter() !== null
}

export type Tasks = {
  /** The tasks due up to the day asked, overdue ones included, ticked ones struck through; `null` while it loads. */
  tasks: Task[] | null
  /** nextasks did not answer, or answered with a problem (EC-1, EC-4, EC-5). */
  problem: string | null
  notices: TaskNotice[]
  /** The key only reads: ticking says why (EC-4). */
  readOnly: boolean
  reload: () => void
}

/** The tasks due up to `to` (a day, "YYYY-MM-DD"), from `from` on (none: overdue ones too), while tasks show at all;
 * `previews`: a repeating task again on its coming days. */
export function useTasks(to: string, { from, previews = false, all = false }: { from?: string; previews?: boolean; all?: boolean } = {}): Tasks {
  const status = useNextasks()
  const chosen = useTaskFilter()
  // `all`: every area chosen in "Verbindungen", whatever the views show (the counts in the sidebar).
  const filter = all ? (status?.connected ? () => true : null) : chosen
  const showing = filter !== null
  const revision = useSyncExternalStore(onNextasks, taskRevision)
  const choice = useSyncExternalStore(onNextasks, choiceRound)
  const [answer, setAnswer] = useState<TaskList | null>(null)
  const [failed, setFailed] = useState(false)
  const [round, setRound] = useState(0)
  const reload = useCallback(() => setRound((value) => value + 1), [])

  useEffect(() => {
    if (!showing) return
    let live = true
    const ask = () => {
      if (isOffline()) return
      nextasksApi.tasks(to, from, previews).then(
        (found) => {
          if (!live) return
          if (found && Array.isArray(found.tasks)) {
            if (Array.isArray(found.areas)) setTaskAreas(found.areas)
            setAnswer(found)
            setFailed(false)
          } else setFailed(true)
        },
        () => live && setFailed(true),
      )
    }
    ask()
    const timer = window.setInterval(ask, TASKS_EVERY_MS)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [showing, to, from, previews, round, choice])

  if (!showing || !filter) return { tasks: [], problem: null, notices: [], readOnly: false, reload }
  if (!answer) return { tasks: failed ? [] : null, problem: failed ? 'nextasks_unreachable' : null, notices: [], readOnly: false, reload }
  return { tasks: merged(answer.tasks, to, revision).filter((task) => filter(task) && (!from || task.day >= from)), problem: answer.problem, notices: answer.notices ?? [], readOnly: answer.level === 'read', reload }
}

/** nextasks' list with what was ticked here: struck through, and kept when nextasks no longer lists it (AC-9). */
function merged(tasks: Task[], to: string, _revision: number): Task[] {
  const ticked = tickedTasks()
  const gone = goneTasks()
  const out = tasks.filter((task) => !gone.has(task.id)).map((task) => {
    const found = task.preview ? undefined : ticked.get(task.id)
    // What was ticked here counts on the day it was ticked; nextasks' state elsewhere (a repeating task moved on).
    const here = found && found.task.day === task.day ? found : undefined
    return here ? { ...task, done: here.done, waiting: here.waiting } : task
  })
  const listed = new Set(out.map((task) => task.id))
  for (const [id, here] of ticked) {
    // A repeating task moved on: its done copy stands on the old day already, under another id.
    const copy = out.some((task) => task.done && task.day === here.task.day && task.title === here.task.title)
    if (!listed.has(id) && !gone.has(id) && !copy && here.task.day <= to) out.push({ ...here.task, done: here.done, waiting: here.waiting })
  }
  return out.sort((a, b) => (a.day === b.day ? (a.time ?? '').localeCompare(b.time ?? '') : a.day.localeCompare(b.day)))
}
