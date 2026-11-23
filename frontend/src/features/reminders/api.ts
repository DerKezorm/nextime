/**
 * The calls of reminders (TIME-7): the area "Erinnerungen" under My account (`/api/reminders`), a calendar's default,
 * this browser's "Auf diesem Gerät erinnern", and the member's own reminders of an event.
 */
import { api } from '../../api/client'
import type { EventDetail } from '../calendars/api'

/** The member's reminders at an event: minutes before the start (a whole day: before its midnight), its own choice or
 * the calendar's default, and the fixed choices of the menu. */
/** ``occurrences``: single events of a series with reminders of their own from a device, which a change here keeps. */
export type ReminderInfo = { minutes: number[]; own: boolean; default: number[]; presets: number[]; occurrences?: string[] }

export type ReminderBrowser = { id: number; browser: string; system: string; installed: boolean; this: boolean; reminders: boolean; quiet: boolean }

export type ReminderCalendar = {
  id: number
  name: string
  color: string
  owner: string | null
  kind: string
  hidden: boolean
  /** `none`, `source` (as in iCloud/Google) or minutes. */
  choice: string
  options: string[]
}

export type ReminderSettings = {
  not_double: boolean
  apple: string[]
  this_browser: { subscribed: boolean; reminders: boolean; quiet: boolean }
  browsers: ReminderBrowser[]
  calendars: ReminderCalendar[]
  delivery: { https: boolean; internet: boolean }
  preview: { title: string; text: string; snooze: string; open: string }
}

export const remindersApi = {
  get: () => api<ReminderSettings>('/api/reminders'),
  notDouble: (on: boolean) => api<ReminderSettings>('/api/reminders', { method: 'PUT', body: { not_double: on } }),
  setDefault: (calendarId: number, choice: string) =>
    api<ReminderSettings>(`/api/reminders/calendars/${calendarId}`, { method: 'PUT', body: { choice } }),
  device: (on: boolean) => api<ReminderSettings>('/api/notify/webpush/reminders', { method: 'PUT', body: { on } }),
  /** The member's own reminders of an event; `null`: as the calendar's default again. */
  forEvent: (eventId: number, minutes: number[] | null) =>
    api<EventDetail>(`/api/events/${eventId}/reminders`, { method: 'PUT', body: { minutes } }),
}
