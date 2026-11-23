/** The search of the server (`backend/app/routers/search.py`, TIME-22). */
import { api } from '../../api/client'
import type { SourceKind } from '../calendars/api'

export type Hit = {
  id: number
  /** For a series: the occurrence it stands for (the next one, or the last one once it ended). */
  occurrence: string | null
  calendar_id: number
  title: string
  recurring: boolean
  /** A declined invitation: gone from the views, still found (EC-15). */
  declined: boolean
  waiting: boolean
  /** Coming or running now (EC-10); otherwise past. */
  coming: boolean
  all_day: boolean
  /** An instant in UTC, or a date for a whole day. */
  start: string
  end: string
  time_zone: string
}

export type Found = {
  total: number
  future_total: number
  offset: number
  items: Hit[]
  /** Connected accounts that do not answer now: searched as last known (EC-8). */
  behind: { kind: SourceKind; label: string; synced_at: string | null }[]
}

export const PAGE = 100

export const searchApi = {
  find: (query: { q: string; calendars: number[] | null; now: number; zone: string; offset: number }) =>
    api<Found>('/api/search', {
      query: {
        q: query.q,
        calendars: query.calendars ? query.calendars.join(',') : undefined,
        now: new Date(query.now).toISOString(),
        zone: query.zone || undefined,
        offset: query.offset,
        limit: PAGE,
      },
    }),
}
