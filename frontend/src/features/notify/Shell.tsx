/**
 * Notifications in the frame of the signed-in app (TIME-21), as nexnotes does it: a browser that allowed
 * notifications is signed up again after a new sign-in, and a tapped notification opens its address in the page that is
 * already open (the service worker hands it over). The address of a notification (`/day?day=2026-12-03&event=12`)
 * shows that day and opens the event (AC-4); a reminder of a series names its occurrence as well (TIME-7 AC-4).
 */
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { usePanel } from '../calendars/store'
import { setViewDay } from '../views/state'
import { insidePath, OPEN_MESSAGE } from './api'
import { pushSupported, renewBrowser } from './browser'

export function NotifyShell() {
  const navigate = useNavigate()
  useEffect(() => {
    if (!pushSupported()) return
    renewBrowser().catch(() => undefined)
    const heard = (event: MessageEvent) => {
      if (event.data?.type !== OPEN_MESSAGE) return
      const path = insidePath(event.data.url)
      if (path) navigate(path)
    }
    navigator.serviceWorker.addEventListener('message', heard)
    return () => navigator.serviceWorker.removeEventListener('message', heard)
  }, [navigate])
  return null
}

const DAY = /^\d{4}-\d{2}-\d{2}$/
const OCCURRENCE = /^\d{8}(T\d{6}Z)?$/

export function OpenFromAddress() {
  const location = useLocation()
  const navigate = useNavigate()
  const { open } = usePanel()
  useEffect(() => {
    const params = new URLSearchParams(location.search)
    const day = params.get('day')
    const event = Number(params.get('event'))
    // A reminder of an occurrence of a series names it (TIME-7 AC-4): `20261126T173000Z`, or `20261126`.
    const occurrence = params.get('occurrence')
    if (!day && !event) return
    if (day && DAY.test(day)) setViewDay(day)
    if (Number.isInteger(event) && event > 0) open({ kind: 'open', id: event, occurrence: occurrence && OCCURRENCE.test(occurrence) ? occurrence : null })
    params.delete('day')
    params.delete('event')
    params.delete('occurrence')
    const rest = params.toString()
    navigate(location.pathname + (rest ? `?${rest}` : ''), { replace: true })
  }, [location.search, location.pathname, navigate, open])
  return null
}
