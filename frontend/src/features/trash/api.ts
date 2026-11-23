/** The trash of the server (`backend/app/routers/trash.py`, TIME-23). */
import { api } from '../../api/client'
import type { CalendarColor, SourceKind } from '../calendars/api'

type Common = {
  id: number
  title: string
  color: CalendarColor
  deleted_at: string
  expires_at: string
  /** Whole days left by the server's clock, rounded up (AC-6). */
  days_left: number
  /** In the last seven days: orange. */
  warn: boolean
  /** Who deleted it: oneself, or by name (the name of then for an account that is gone, EC-12). */
  deleted_by: { self: boolean; name: string } | null
  /** Deleted through a key of a program (TIME-26 AC-10). */
  deleted_via?: string
  /** Why it went when nobody deleted it: its source brought the same event, or its organizer cancelled it. */
  reason?: 'source_has_it' | 'cancelled' | ''
}

export type TrashCalendar = Common & { kind: 'calendar'; events: number }

export type TrashEvent = Common & {
  kind: 'event'
  calendar: { id: number; name: string; source: SourceKind | '' }
  /** Its calendar is gone by now: restoring asks for another one (EC-1, EC-9). */
  calendar_gone: boolean
  all_day: boolean
  /** An instant, or a date for a whole day. */
  start: string
  time_zone: string
  recurring: boolean
  /** A series with changed or cancelled occurrences: they come back with it (AC-4). */
  exceptions: boolean
}

/** One occurrence of a series, or this one and the following, deleted on their own (TIME-5 AC-6). */
export type TrashPart = Omit<TrashEvent, 'kind'> & {
  kind: 'part'
  part: 'occurrence' | 'following'
  /** Its series is in the trash itself or its calendar is gone: the series comes back first. */
  series_gone: boolean
}

export type TrashItem = TrashCalendar | TrashEvent | TrashPart

export type TrashList = {
  /** The server's time of the list: emptying takes only what was deleted until then. */
  now: string
  trash_days: number
  warn_days: number
  total: number
  offset: number
  items: TrashItem[]
}

export const PAGE = 100

export const trashApi = {
  list: (offset = 0) => api<TrashList>('/api/trash', { query: { offset, limit: PAGE } }),
  restore: (item: Pick<TrashItem, 'kind' | 'id'>, calendarId?: number) =>
    api<{ id: number; calendar_id?: number }>(`/api/trash/${item.kind}s/${item.id}/restore`, {
      method: 'POST',
      body: calendarId !== undefined ? { calendar_id: calendarId } : {},
    }),
  empty: (until: string) => api<{ events: number; calendars: number }>('/api/trash/empty', { method: 'POST', body: { until } }),
}
