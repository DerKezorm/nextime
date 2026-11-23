/**
 * The calls of sharing (TIME-19): who a calendar is shared with and how, the guest link, "Shares" under Settings, and
 * what a guest with a read link sees (without an account).
 */
import { api } from '../../api/client'

export type ShareRight = 'read' | 'edit' | 'busy'
/** `own` for nextime's own calendars, else the kind of its source. */
export type ShareKind = 'own' | 'icloud' | 'google' | 'caldav'

export type SharePerson = { id: number; name: string; email: string; color: number | null; avatar: string | null }
export type GuestLink = { url: string | null; feed: string | null; since: string; expires_on: string | null }

export type Sharing = {
  calendar: { id: number; name: string; color: string; icon?: string; kind: ShareKind }
  rights: ShareRight[]
  people: (SharePerson & { right: ShareRight; since: string })[]
  links_allowed: boolean
  link: GuestLink | null
}

export type SharedCalendar = { id: number; name: string; color: string; icon?: string; kind: string }
export type MineRow =
  | { kind: 'person'; calendar: SharedCalendar; person: SharePerson; right: ShareRight; since: string }
  | { kind: 'link'; calendar: SharedCalendar; since: string; until: string | null; active: boolean }
export type WithMeRow = { calendar: SharedCalendar; owner: string; right: ShareRight }
export type ShareNotice = { id: number; calendar: string; owner: string; color: string; at: string }
export type SharesOverview = { mine: MineRow[]; with_me: WithMeRow[]; notices: ShareNotice[] }

export const sharesApi = {
  of: (calendarId: number) => api<Sharing>(`/api/calendars/${calendarId}/sharing`),
  people: (query: string) => api<{ people: SharePerson[] }>('/api/shares/people', { query: { q: query } }).then((answer) => answer.people),
  set: (calendarId: number, personId: number, right: ShareRight) => api<Sharing>(`/api/calendars/${calendarId}/sharing/${personId}`, { method: 'PUT', body: { right } }),
  remove: (calendarId: number, personId: number) => api<Sharing>(`/api/calendars/${calendarId}/sharing/${personId}`, { method: 'DELETE' }),
  setLink: (calendarId: number, expiresOn: string | null) => api<Sharing>(`/api/calendars/${calendarId}/guest-link`, { method: 'PUT', body: { expires_on: expiresOn } }),
  renewLink: (calendarId: number) => api<Sharing>(`/api/calendars/${calendarId}/guest-link/renew`, { method: 'POST' }),
  revokeLink: (calendarId: number) => api<Sharing>(`/api/calendars/${calendarId}/guest-link`, { method: 'DELETE' }),
  leave: (calendarId: number) => api<void>(`/api/calendars/${calendarId}/leave`, { method: 'POST' }),
  overview: () => api<SharesOverview>('/api/shares'),
  dismiss: (noticeId: number) => api<void>(`/api/shares/notices/${noticeId}`, { method: 'DELETE' }),
}

/** One coming event on the guest page: an instant in UTC, or a day for a whole day (`end` its last day). */
export type GuestEvent = { all_day: boolean; start: string; end: string; title: string; location: string }
export type GuestPage = { calendar: { name: string; color: string }; owner: string; events: GuestEvent[]; more: boolean; next: number; feed: string }

export const guestApi = {
  page: (token: string, offset = 0) => api<GuestPage>(`/api/guest/${encodeURIComponent(token)}`, { query: { offset } }),
}

/** The shareable kinds of calendar (AC-2, AC-12): own ones and those from iCloud, Google, CalDAV; not subscriptions. */
export function shareKind(right: string, origin: { kind: string } | null | undefined): ShareKind | null {
  if (right !== 'owner') return null
  if (!origin) return 'own'
  return origin.kind === 'icloud' || origin.kind === 'google' || origin.kind === 'caldav' ? origin.kind : null
}

/** The day `count` days from today, as `YYYY-MM-DD` on this device's clock. */
export function dayFrom(count: number): string {
  const date = new Date()
  date.setDate(date.getDate() + count)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
