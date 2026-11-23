/** Export, import and moving house of the server (`backend/app/routers/transfer.py`, TIME-24). */
import { api } from '../../api/client'
import type { CalendarColor } from '../calendars/api'

export type Failed = {
  title: string
  reason: 'attachment_too_large' | 'attachment_unavailable' | 'attachment_unreadable' | 'event_too_deep' | 'event_too_large'
}

/** A job of the server (taking a file, moving a calendar): how far it is, and how it ended. */
export type JobState = {
  state: 'ready' | 'running' | 'done' | 'failed'
  done: number
  total: number
  /** Why it failed (an error code of the server). */
  problem: string | null
  result: ImportResult | MoveResult | null
}

export type ImportPreview = JobState & {
  id: string
  name: string
  events: number
  /** The first and last day of the events (`YYYY-MM-DD`). */
  first: string | null
  last: string | null
  /** Entries that are no events (tasks, journal), left out (EC-6). */
  skipped: number
  /** Embedded files over the limit (EC-7). */
  too_large: number
  /** How the file was read: `cp1252` for an older Windows file (EC-4). */
  encoding?: string
  /** Already in the calendar asked for, or twice in the file: skipped (AC-2). */
  existing: number
  new: number
  account_events: number
  limit: number
  over_limit: boolean
}

export type ImportResult = { made: number; existing: number; shortened?: number; failed: Failed[]; calendar: { id: number; name: string } }

export type Movable = { id: number; name: string; color: CalendarColor; kind: 'icloud' | 'google' }

export type MovePreview = {
  calendar: { id: number; name: string; color: CalendarColor }
  kind: 'icloud' | 'google'
  events: number
  /** Invitations not accepted: they stay (AC-8). */
  invitations: number
  moved_to: { id: number; name: string } | null
  shared: boolean
  state: string
  account_events: number
  limit: number
  over_limit: boolean
}

export type MoveResult = {
  made: number
  existing: number
  failed: Failed[]
  invitations: number
  again: boolean
  kind: 'icloud' | 'google'
  shared: boolean
  from: { id: number; name: string }
  calendar: { id: number; name: string }
}

/** How often the page asks how far a job is. */
export const POLL_MS = 1000

/**
 * Follows a job from its first answer until it is done or failed, asking every `POLL_MS`; `onState` hears each state,
 * `alive` stops asking (the page went). A failed question (no connection for a moment) asks again.
 */
export async function follow(first: JobState, ask: () => Promise<JobState>, onState: (state: JobState) => void, alive: () => boolean, every = POLL_MS): Promise<JobState> {
  let state = first
  onState(state)
  while (state.state === 'running' && alive()) {
    await new Promise((resolve) => window.setTimeout(resolve, every))
    try {
      state = await ask()
      onState(state)
    } catch {
      // Asked again at the next round; the job runs on the server all the same.
    }
  }
  return state
}

export const transferApi = {
  exportPath: (calendarId: number) => `/api/calendars/${calendarId}/export.ics`,
  zipPath: '/api/export.zip',
  take: (file: File) => api<ImportPreview>('/api/imports', { method: 'POST', query: { name: file.name }, raw: file, headers: { 'Content-Type': 'text/calendar' } }),
  preview: (id: string, calendarId: number) => api<ImportPreview>(`/api/imports/${id}`, { query: { calendar_id: calendarId } }),
  status: (id: string) => api<ImportPreview>(`/api/imports/${id}`),
  apply: (id: string, calendarId: number) => api<JobState>(`/api/imports/${id}/apply`, { method: 'POST', body: { calendar_id: calendarId } }),
  movable: () => api<{ calendars: Movable[] }>('/api/moves'),
  movePreview: (calendarId: number) => api<MovePreview>(`/api/calendars/${calendarId}/move`),
  move: (calendarId: number, clientRef: string) => api<JobState>(`/api/calendars/${calendarId}/move`, { method: 'POST', body: { client_ref: clientRef } }),
  moveStatus: (clientRef: string) => api<JobState>(`/api/moves/${encodeURIComponent(clientRef)}`),
}
