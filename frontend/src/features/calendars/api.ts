/**
 * The calls of the calendar features (TIME-4, TIME-5, TIME-6, TIME-20) and their types. Times travel as the server
 * names them: `YYYY-MM-DD` for a whole day (the end is the last day), otherwise `YYYY-MM-DDTHH:MM` on the clock of
 * `time_zone`; occurrences in a window come with instants in UTC.
 */
import type { NewDefaults } from './defaults'
import { api } from '../../api/client'
import type { AttachmentInfo, RemoteAttachment } from '../attachments/api'
import type { Point } from '../places/api'
import type { ReminderInfo } from '../reminders/api'

export const CALENDAR_COLORS = ['slate', 'red', 'orange', 'yellow', 'green', 'teal', 'blue', 'violet', 'pink', 'brown', 'gray'] as const
/** One of the eleven of the design system. */
export type PaletteColor = (typeof CALENDAR_COLORS)[number]
/** One of the eleven, or an own color as `#rrggbb` (TIME-4, as nexnotes). */
export type CalendarColor = PaletteColor | `#${string}`
export type Group = 'own' | 'icloud' | 'google' | 'caldav' | 'shared' | 'subscriptions' | 'nextasks'
/** The groups of the list in the order of the sidebar (TIME-4 AC-9, TIME-15 AC-9): every CalDAV account has a group
 * of its own. */
export const GROUPS: Group[] = ['own', 'icloud', 'google', 'caldav', 'shared', 'subscriptions', 'nextasks']
export type Right = 'owner' | 'edit' | 'read' | 'busy'

/** Where a connected calendar comes from (TIME-15, TIME-16, TIME-17). */
export type SourceKind = 'icloud' | 'caldav' | 'google' | 'subscription' | 'holidays'
export type SourceState = 'ok' | 'expired' | 'unreachable' | 'paused'
/** The source of a calendar, for the one who connected it: its group, "waiting", "state of 15:52" (TIME-15 EC-5). */
export type Origin = { id: number; kind: SourceKind; label: string; state: SourceState; synced_at: string | null }

export type CalendarEntry = {
  id: number
  name: string
  color: CalendarColor
  /** Its symbol (a name of `lib/symbols.ts`), drawn in its color; empty: the color square (TIME-4, as nexnotes). */
  icon?: string
  background: boolean
  /** "Highlight its days", the person's own: the days of its background events stand out in month and week. As from
   * the start on for the built-in public holidays (TIME-17) only. */
  holiday?: boolean
  reminder: number
  /** What a new event of it starts with, for this account (TIME-4, `features/calendars/defaults.ts`). */
  defaults?: NewDefaults
  group: Group
  source: string
  right: Right
  writable: boolean
  hidden: boolean
  position: number
  default: boolean
  owner: string | null
  shared: boolean
  events: number
  /** Only to read at its source (an iCloud calendar shared to read, a subscription, holidays). */
  read_only?: boolean
  origin?: Origin | null
}

export type CalendarList = {
  calendars: CalendarEntry[]
  default_id: number | null
  default_moved: 'deleted' | 'hidden' | 'read_only' | null
  default_was: string | null
  count: number
  smooth_up_to: number
  reminders: number[]
  /** The operator's address suggestions and maps (TIME-8 AC-6); the largest attachment in MB (TIME-9 AC-1). */
  places?: boolean
  attachment_max_mb?: number
  created?: number
  deleted_events?: number
}

export type CalendarForm = {
  name: string
  color: CalendarColor
  icon: string
  background: boolean
  default: boolean
  reminder: number
  defaults?: NewDefaults
  /** "Highlight its days", sent only when the person changed it: left out, the server keeps what holds. */
  holiday?: boolean
}

export type Person = { id: number; name: string; color: number | null; avatar: string | null }

/** One place an event stands in: its calendar, the event and occurrence there, whether the member may change it. */
export type Source = { id: number; occurrence: string | null; calendar_id: number; writable: boolean; etag: string }

/** The year as a heat map: the number of events of each day that has any, and the days of calendars with "Tage
 * hervorheben" (TIME-12 AC-17, AC-20). */
export type YearMap = { year: number; zone: string; days: Record<string, number>; holidays: string[] }

export type Occurrence = {
  id: number
  occurrence: string | null
  calendar_id: number
  title: string
  /** Empty for "busy only" (TIME-14 AC-5). */
  location?: string
  busy: boolean
  busy_only: boolean
  recurring: boolean
  all_day: boolean
  start: string
  end: string
  time_zone: string
  writable?: boolean
  etag?: string
  /** Who entered it, in a shared calendar and not the member itself; for "busy only" the owner (TIME-14 AC-2). */
  person?: Person | null
  /** The same event in two calendars of one person, shown once: every place it stands in (TIME-14 AC-3). */
  sources?: Source[]
  /** A change that has not reached iCloud, Google or the CalDAV server yet (TIME-15 EC-2). */
  waiting?: boolean
  /** The member's invitation in it (TIME-18); a declined one does not come. */
  invitation?: { state: InvitationState; by: string } | null
}

export type InvitationState = 'open' | 'accepted' | 'tentative' | 'declined'
export type Answer = 'accepted' | 'tentative' | 'declined'

/** A person in an event with attendees (TIME-18 AC-3). */
export type Attendee = { name: string; address: string; state: InvitationState; organizer: boolean; self: boolean }

export type InvitationDetail = {
  state: InvitationState
  by: string
  cancelled: boolean
  source: SourceKind | ''
  can_answer: boolean
  why_not: 'no_replies' | 'locked' | 'read_only_source' | 'by_mail' | ''
}

export type Weekday = 'MO' | 'TU' | 'WE' | 'TH' | 'FR' | 'SA' | 'SU'
export type Rule = {
  freq: 'daily' | 'weekly' | 'monthly' | 'yearly'
  interval: number
  weekdays: Weekday[]
  monthly: 'date' | 'nth' | 'last'
  end: 'never' | 'until' | 'count'
  until: string | null
  count: number | null
}


export type Summary = { title: string; all_day: boolean; start: string; end: string; time_zone: string }

export type EventDetail = {
  id: number
  etag: string
  calendar_id: number
  occurrence: string | null
  first: boolean
  title: string
  notes: string
  location: string
  busy: boolean
  all_day: boolean
  start: string
  end: string
  time_zone: string
  recurring: boolean
  rule: Rule | null
  rrule: string
  series_start: { all_day: boolean; start: string; end: string; time_zone: string }
  right: Right
  writable: boolean
  busy_only: boolean
  created_by: Person | null
  updated_by: Person | null
  /** The key of a program the last change came through (TIME-26 AC-10); empty otherwise. */
  updated_via?: string
  updated_at: string
  shared: boolean
  conflict: { by: Person | null; self: boolean; mine: Summary; theirs: Summary } | null
  waiting?: boolean
  source?: SourceKind | null
  attendees?: Attendee[]
  attendees_count?: number
  invitation?: InvitationDetail | null
  /** Attendees from a subscription, which takes no answers (TIME-18 EC-2). */
  no_answers?: 'read_only_source'
  /** Where a chosen place lies (TIME-8). */
  place?: Point | null
  /** The member's own reminders (TIME-7). */
  reminders?: ReminderInfo
  /** The files at the event and whether the member may add some (TIME-9). */
  attachments?: AttachmentInfo[]
  attachments_allowed?: boolean
  attachment_max_mb?: number
  remote_attachments?: RemoteAttachment[]
}

/** What the side panel sends: only what changed when changing (`rule` present, even null, when it changed). */
export type EventFields = {
  calendar_id?: number
  title?: string
  all_day?: boolean
  start?: string
  end?: string
  time_zone?: string
  busy?: boolean
  notes?: string
  rule?: Rule | null
  location?: string
  geo?: Point | null
  /** A new event's own reminders; left out: as the calendar's default (TIME-7). */
  reminders?: number[]
}

export type Scope = 'this' | 'following' | 'all'

export const calendarsApi = {
  list: () => api<CalendarList>('/api/calendars'),
  create: (form: CalendarForm, clientRef: string) => api<CalendarList>('/api/calendars', { method: 'POST', body: { ...form, client_ref: clientRef } }),
  change: (id: number, change: Partial<CalendarForm & { hidden: boolean }>) => api<CalendarList>(`/api/calendars/${id}`, { method: 'PATCH', body: change }),
  order: (ids: number[]) => api<CalendarList>('/api/calendars/order', { method: 'PUT', body: { ids } }),
  remove: (id: number) => api<CalendarList>(`/api/calendars/${id}`, { method: 'DELETE' }),
}

export const eventsApi = {
  /** The occurrences in [start, end); `calendars` only these (the collection chosen, TIME-13), `hidden` with hidden
   * ones (one calendar shown alone, AC-11). */
  window: (start: Date, end: Date, only?: { calendars: number[] | null; hidden?: boolean }, signal?: AbortSignal) =>
    api<Occurrence[]>('/api/events', {
      query: {
        start: start.toISOString(),
        end: end.toISOString(),
        calendars: only?.calendars ? only.calendars.join(',') : undefined,
        hidden: only?.hidden ? 'true' : undefined,
      },
      signal,
    }),
  /** How many events each day of `year` holds in `zone`, for the calendars shown (TIME-12 AC-16 to AC-18). */
  year: (year: number, zone: string, only?: { calendars: number[] | null; hidden?: boolean }, signal?: AbortSignal) =>
    api<YearMap>('/api/events/year', {
      query: {
        year: String(year),
        zone,
        calendars: only?.calendars ? only.calendars.join(',') : undefined,
        hidden: only?.hidden ? 'true' : undefined,
      },
      signal,
    }),
  get: (id: number, occurrence?: string | null) => api<EventDetail>(`/api/events/${id}`, { query: { occurrence: occurrence ?? undefined } }),
  create: (fields: EventFields, clientRef: string) => api<EventDetail>('/api/events', { method: 'POST', body: { ...fields, client_ref: clientRef } }),
  change: (id: number, fields: EventFields & { scope: Scope; occurrence?: string | null; etag: string }) =>
    api<EventDetail>(`/api/events/${id}`, { method: 'PATCH', body: fields }),
  /** Into the trash (TIME-23); the answer names the entry it became, for "Undo". */
  remove: (id: number, scope: Scope, occurrence?: string | null) =>
    api<{ trash?: { kind: 'event' | 'part'; id: number } } | undefined>(`/api/events/${id}`, { method: 'DELETE', query: { scope, occurrence: occurrence ?? undefined } }),
  conflict: (id: number, restore: boolean) => api<EventDetail>(`/api/events/${id}/conflict`, { method: 'POST', body: { restore } }),
  /** The answer to an invitation (TIME-18 AC-2, AC-5); `etag` the version seen, so a changed one shows anew (EC-1). */
  answer: (id: number, answer: Answer, scope: 'this' | 'all', occurrence: string | null, etag: string) =>
    api<EventDetail>(`/api/events/${id}/answer`, { method: 'POST', body: { answer, scope, occurrence: occurrence ?? undefined, etag } }),
}

/** `task_areas`: the areas of nextasks in it, `["*"]` for every one (TIME-25 area rows); `with_tasks`: any. */
export type Collection = { id: number; name: string; calendar_ids: number[]; with_tasks?: boolean; task_areas?: string[]; updated_at: string }
export type CollectionAnswer = { collections: Collection[]; created?: number }

export const collectionsApi = {
  list: () => api<CollectionAnswer>('/api/collections'),
  create: (name: string, calendarIds: number[], clientRef: string, taskAreas: string[] = []) =>
    api<CollectionAnswer>('/api/collections', { method: 'POST', body: { name, calendar_ids: calendarIds, client_ref: clientRef, task_areas: taskAreas } }),
  change: (id: number, name: string, calendarIds: number[], taskAreas: string[] = []) =>
    api<CollectionAnswer>(`/api/collections/${id}`, { method: 'PATCH', body: { name, calendar_ids: calendarIds, task_areas: taskAreas } }),
  remove: (id: number) => api<CollectionAnswer>(`/api/collections/${id}`, { method: 'DELETE' }),
}

export type DeviceRow = { id: number; name: string; client: string; created_at: string; last_used_at: string | null }
export type ComingAlong = { id: number; name: string; color: CalendarColor; right: Right }
export type DeviceOverview = { devices: DeviceRow[]; https: boolean; address: string; user: string; calendars: ComingAlong[]; minutes: number }
export type NewDevice = {
  device: DeviceRow
  password: string
  address: string
  user: string
  code: string
  link: string
  qr: string
  minutes: number
  devices: DeviceRow[]
}
export type SetupPage = { device: string; account: string; calendars: ComingAlong[]; expires_at: string }
export type Credentials = { address: string; user: string; password: string; device: string }

export const devicesApi = {
  list: () => api<DeviceOverview>('/api/devices'),
  create: (name: string) => api<NewDevice>('/api/devices', { method: 'POST', body: { name } }),
  lockOut: (id: number) => api<void>(`/api/devices/${id}`, { method: 'DELETE' }),
  setup: (code: string) => api<SetupPage>(`/api/device-setup/${encodeURIComponent(code)}`),
  credentials: (code: string, name: string) => api<Credentials>(`/api/device-setup/${encodeURIComponent(code)}/credentials`, { method: 'POST', body: { name } }),
}

/** Where the profile for an Apple device is loaded from (a download, so the browser goes there itself). */
export function profileAddress(code: string, name: string): string {
  return `/api/device-setup/${encodeURIComponent(code)}/profile?name=${encodeURIComponent(name)}`
}

/** Leaving the page for a download the browser handles itself (the profile); one place, so tests can watch it. */
export const browser = {
  go(url: string): void {
    window.location.assign(url)
  },
}

/** A fresh id for "the same request again" (a second click, a retry after the connection broke). */
export function newRef(): string {
  const bytes = new Uint8Array(12)
  crypto.getRandomValues(bytes)
  return 'ref-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}
