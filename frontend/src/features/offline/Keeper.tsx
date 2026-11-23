/**
 * Takes the snapshot for reading without a connection (TIME-28 AC-3) while the installed app runs with a connection:
 * at the start, after changes (bundled, at most every `GAP_MS`), when the connection comes back (AC-4) and when the app
 * comes back to the front (EC-9). When the connection comes back, every view asks again at once and the hint goes
 * (AC-4).
 */
import { useEffect } from 'react'

import { useMe } from '../../state/auth'
import { useDisplay } from '../../lib/useDisplay'
import { announce, useRevision } from '../calendars/store'
import { usePwa } from '../../pwa/worker'
import { onReconnect, useOffline } from './connection'
import { GAP_MS, lastTakenAt, SETTLE_MS, takeSnapshot } from './take'

export function OfflineKeeper() {
  const me = useMe()
  const offline = useOffline()
  const seen = useRevision()
  const { standalone } = usePwa()
  const { zone } = useDisplay()

  // The connection came back: every view asks again, and the hint goes (AC-4).
  useEffect(() => onReconnect(announce), [])

  useEffect(() => {
    if (!me || offline || !standalone) return
    const last = lastTakenAt()
    const wait = Math.max(SETTLE_MS, GAP_MS - (Date.now() - last))
    const timer = window.setTimeout(() => void takeSnapshot(zone, me), last === 0 ? 0 : wait)
    return () => window.clearTimeout(timer)
    // `me` stands for the account and its display; `seen` for every change.
  }, [me, offline, standalone, zone, seen])
  return null
}
