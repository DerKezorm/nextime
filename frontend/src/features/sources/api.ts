/**
 * The calls of the connected sources (TIME-15 iCloud and CalDAV, TIME-16 Google, TIME-17 subscriptions and holidays),
 * the open invitations (TIME-18) and the operator's Google set-up, with their types.
 */
import { api } from '../../api/client'
import type { CalendarColor, SourceKind, SourceState, Summary } from '../calendars/api'

export type SourceCalendar = { id: number; name: string; color: CalendarColor; read_only: boolean }

export type SourceRow = {
  id: number
  kind: SourceKind
  label: string
  calendars: SourceCalendar[]
  state: SourceState
  /** Since when the state holds. */
  since: string | null
  /** The last good round: "the state of …". */
  synced_at: string | null
  /** Why: `credentials`, `key`, `setup_changed`, `google_off`, `subscription_gone` and the like. */
  problem: string
  /** Changes that have not reached the source yet (TIME-15 EC-8). */
  waiting: number
  /** Events the source sent that nextime could not read (EC-18). */
  unreadable: number
  /** Calendars offered as "new" (AC-14). */
  new: number
  /** The first fetch is still running (EC-12). */
  loading: boolean
  events: number
  holidays?: { country: string; regions: string[]; school: boolean }
  /** A subscription through Google's secret address (TIME-16 AC-4). */
  google?: boolean
}

/** A change the source refused for good (TIME-15 EC-6); `lost` null when the refused change was deleting. */
export type Notice = { id: number; title: string; source: SourceKind; calendar: string; calendar_id: number | null; lost: Summary | null; created_at: string }

export type Sources = {
  sources: SourceRow[]
  notices: Notice[]
  events_total: number
  events_smooth_up_to: number
  /** Set up and switched on: "Connect with Google"; not set up: the read-only way; set up and off: no way at all. */
  google: { ready: boolean; configured: boolean }
  created?: number
  created_event?: number | null
}

export type Choice = { url: string; name: string; color: CalendarColor; read_only: boolean; new: boolean; chosen: boolean }
export type Choices = { source_id: number; kind: SourceKind; label: string; calendars: Choice[] }

export type Preview = {
  name: string
  count: number
  first: string | null
  last: string | null
  /** Over 20,000 events with it (TIME-17 EC-2). */
  over: boolean
  events_total: number
  events_smooth_up_to: number
  exists: number | null
}

export type Login = { username?: string; password?: string }

export type HolidayCountry = { code: string; name: string; school: boolean; regions: { code: string; name: string }[] }

export type Invitation = { id: number; occurrence: string | null; calendar_id: number; title: string; start: string; end: string; all_day: boolean; by: string }

export type GoogleSetup = {
  client_id: string
  secret_set: boolean
  redirect_uri: string
  https: boolean
  ready: boolean
  enabled: boolean
  members: number
  redirect_changed: boolean
}

export const sourcesApi = {
  list: () => api<Sources>('/api/sources'),
  connect: (kind: 'icloud' | 'caldav', server: string, username: string, password: string, clientRef: string) =>
    api<Choices>('/api/sources/dav', { method: 'POST', body: { kind, server, username, password, client_ref: clientRef } }),
  choices: (id: number) => api<Choices>(`/api/sources/${id}/calendars`),
  /** `dropWaiting`: calendars left out still have changes waiting, the member saw how many (TIME-15 EC-8). */
  choose: (id: number, urls: string[], dropWaiting = false) =>
    api<Sources>(`/api/sources/${id}/calendars`, { method: 'PUT', body: dropWaiting ? { urls, drop_waiting: true } : { urls } }),
  reconnect: (id: number, password: string) => api<Sources>(`/api/sources/${id}/reconnect`, { method: 'POST', body: { password } }),
  refresh: (id: number) => api<Sources>(`/api/sources/${id}/refresh`, { method: 'POST' }),
  /** "Aus nextime entfernen": one calendar left out, as in "Kalender wählen"; at the source it stays. */
  leave: (id: number, calendarId: number, dropWaiting: boolean) =>
    api<Sources>(`/api/sources/${id}/calendars/${calendarId}`, { method: 'DELETE', query: { drop_waiting: dropWaiting ? 'true' : undefined } }),
  remove: (id: number, dropWaiting: boolean) => api<Sources>(`/api/sources/${id}`, { method: 'DELETE', query: { drop_waiting: dropWaiting ? 'true' : undefined } }),
  preview: (url: string, login: Login, google = false) => api<Preview>('/api/sources/subscriptions/preview', { method: 'POST', body: { url, ...login, google } }),
  subscribe: (url: string, name: string, background: boolean, login: Login, google: boolean, clientRef: string) =>
    api<Sources>('/api/sources/subscriptions', { method: 'POST', body: { url, name, background, ...login, google, client_ref: clientRef } }),
  link: (id: number, url: string) => api<Sources>(`/api/sources/${id}/link`, { method: 'PUT', body: { url } }),
  countries: () => api<{ countries: HolidayCountry[] }>('/api/sources/holidays'),
  holidays: (country: string, regions: string[], school: boolean) => api<Sources>('/api/sources/holidays', { method: 'PUT', body: { country, regions, school } }),
  googleStart: (sourceId?: number) => api<{ url: string }>('/api/sources/google/start', { method: 'POST', body: { source_id: sourceId ?? null } }),
  settle: (id: number, create: boolean) => api<Sources>(`/api/sources/notices/${id}`, { method: 'POST', body: { create } }),
  invitations: () => api<Invitation[]>('/api/invitations'),
}

export const googleSetupApi = {
  read: () => api<GoogleSetup>('/api/server/google'),
  save: (clientId: string, clientSecret: string, confirm: boolean) =>
    api<GoogleSetup>('/api/server/google', { method: 'PUT', body: { client_id: clientId, client_secret: clientSecret, confirm } }),
  remove: () => api<GoogleSetup>('/api/server/google', { method: 'DELETE', query: { confirm: 'true' } }),
}

/** Kinds that connect with an account and choose calendars (TIME-15 AC-9). */
export const CHOOSING: SourceKind[] = ['icloud', 'caldav', 'google']
