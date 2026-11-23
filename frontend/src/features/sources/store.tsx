/**
 * What the pages share about the connected sources: the rows of "Calendars and accounts", the notices about refused
 * changes and whether Google is set up, asked again with every change (a live change from elsewhere too); the open
 * invitations; and a moment as the hints write it ("15:52", "gestern 18:02"). Short messages at the bottom of the page
 * are `lib/notice.ts`.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useDisplay } from '../../lib/useDisplay'
import type { SourceKind } from '../calendars/api'
import { announce, useRevision } from '../calendars/store'
import { clockText, wallOf } from '../calendars/time'
import { addDays, format } from '../views/dates'
import { sourcesApi, type Invitation, type Sources } from './api'

/** The sources of the account; `null` while they come. */
export function useSources(): { data: Sources | null; failed: boolean; take: (data: Sources) => void } {
  const seen = useRevision()
  const [data, setData] = useState<Sources | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let live = true
    sourcesApi
      .list()
      .then((answer) => {
        if (!live) return
        if (!answer || !Array.isArray(answer.sources)) return setFailed(true)
        setData(answer)
        setFailed(false)
      })
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [seen])
  const take = useCallback((answer: Sources) => {
    setData(answer)
    // Calendars came or went: the sidebar and the views ask again.
    announce()
  }, [])
  return { data, failed, take }
}

/** The open invitations from now on (TIME-18 AC-1, EC-9). */
export function useInvitations(): Invitation[] {
  const seen = useRevision()
  const [list, setList] = useState<Invitation[]>([])
  useEffect(() => {
    let live = true
    sourcesApi
      .invitations()
      .then((answer) => live && setList(Array.isArray(answer) ? answer : []))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [seen])
  return list
}

/** The name of a kind of source as the hints say it: "iCloud", "Google", "CalDAV", "Abo", "Feiertage". */
export function useKindName(): (kind: SourceKind | '') => string {
  const { t } = useTranslation()
  return (kind) => (kind ? t(`sources.kinds.${kind}`) : '')
}

/** A moment as the hints write it: "15:52" today, "gestern 18:02", else "Mo., 23.11. 18:02"; on the account's clock. */
export function useStamp(): (iso: string | null) => string {
  const { t } = useTranslation()
  const { zone, twelve, language } = useDisplay()
  return (iso) => {
    if (!iso) return ''
    const ms = Date.parse(iso)
    const wall = wallOf(ms, zone)
    const time = clockText(wall.time, language, twelve)
    const today = wallOf(Date.now(), zone).date
    if (wall.date === today) return time
    if (wall.date === addDays(today, -1)) return t('sources.yesterdayAt', { time })
    return `${format(wall.date, language, { weekday: 'short', day: 'numeric', month: 'numeric' })} ${time}`
  }
}

/** Calendar names as a sentence lists them: "Arbeit und Persönlich", each in quotes when asked. */
export function useNames(): (names: string[], quoted?: boolean) => string {
  const { t, i18n } = useTranslation()
  return (names, quoted = false) => {
    const shown = quoted ? names.map((name) => t('sources.quoted', { name })) : names
    try {
      return new Intl.ListFormat(i18n.language, { style: 'long', type: 'conjunction' }).format(shown)
    } catch {
      return shown.join(', ')
    }
  }
}
