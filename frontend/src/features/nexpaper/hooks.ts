/**
 * nexpaper for any part of the page: the connection, whether deadlines show at all, and the open deadlines up to a
 * day, asked when a view opens and every minute (the server keeps nexpaper's answer a minute as well).
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'

import { useShown } from '../collections/store'
import { isOffline } from '../offline/connection'
import { DEADLINES_ROW, loadNexpaper, nexpaperApi, nexpaperStatus, onNexpaper, type Deadline, type DeadlineList, type NexpaperStatus } from './api'

export const DEADLINES_EVERY_MS = 60_000

export function useNexpaper(): NexpaperStatus | null {
  const status = useSyncExternalStore(onNexpaper, nexpaperStatus)
  useEffect(() => {
    if (!nexpaperStatus() && !isOffline()) void loadNexpaper().catch(() => undefined)
  }, [])
  return status
}

/** Whether deadlines show, as the tasks of nextasks do: connected, not one calendar alone; "Meine Fristen" alone shows
 * them only, an area of nextasks alone none; hidden by its eye none; a collection holds calendars only. */
export function useDeadlinesShown(): boolean {
  const status = useNexpaper()
  const shown = useShown()
  if (!status?.connected || shown.only) return false
  if (shown.onlyArea) return shown.onlyArea.id === DEADLINES_ROW
  return !status.hidden && !shown.collection
}

export type Deadlines = {
  /** The open deadlines up to the day asked, overdue ones included; `null` while they load. */
  deadlines: Deadline[] | null
  /** nexpaper did not answer, or answered with a problem. */
  problem: string | null
  reload: () => void
}

/** The open deadlines up to `to` ("YYYY-MM-DD"), from `from` on (none: overdue ones too), while they show at all;
 * `all`: whatever the views show (the count in the sidebar). */
export function useDeadlines(to: string, { from, all = false }: { from?: string; all?: boolean } = {}): Deadlines {
  const status = useNexpaper()
  const chosen = useDeadlinesShown()
  const showing = all ? Boolean(status?.connected) : chosen
  const [answer, setAnswer] = useState<DeadlineList | null>(null)
  const [failed, setFailed] = useState(false)
  const [round, setRound] = useState(0)
  const reload = useCallback(() => setRound((value) => value + 1), [])

  useEffect(() => {
    if (!showing) return
    let live = true
    const ask = () => {
      if (isOffline()) return
      nexpaperApi.deadlines(to, from).then(
        (found) => {
          if (!live) return
          if (found && Array.isArray(found.deadlines)) {
            setAnswer(found)
            setFailed(false)
          } else setFailed(true)
        },
        () => live && setFailed(true),
      )
    }
    ask()
    const timer = window.setInterval(ask, DEADLINES_EVERY_MS)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [showing, to, from, round])

  if (!showing) return { deadlines: [], problem: null, reload }
  if (!answer) return { deadlines: failed ? [] : null, problem: failed ? 'nexpaper_unreachable' : null, reload }
  return { deadlines: answer.deadlines.filter((one) => one.day <= to && (!from || one.day >= from)), problem: answer.problem, reload }
}
