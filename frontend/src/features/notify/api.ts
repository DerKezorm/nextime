/** The calls of notifications (TIME-21): the settings of a calendar, and this browser's sign-up for Web Push. */
import { api } from '../../api/client'

export type NotifyBrowser = { id: number; browser: string; system: string; installed: boolean; this: boolean }
export type NotifyText = { title: string; text: string }

export type NotifySettings = {
  calendar: { id: number; name: string; color: string; right: string; owner: string | null; kind: string }
  shared: boolean
  on: boolean
  devices: { browsers: NotifyBrowser[]; this_browser: boolean }
  delivery: { https: boolean; internet: boolean }
  preview: { bundle: NotifyText; busy: NotifyText }
}

type Subscription = { endpoint: string; keys: { p256dh: string; auth: string }; installed: boolean; reminders: boolean; platform: string }

export const notifyApi = {
  get: (calendarId: number) => api<NotifySettings>(`/api/calendars/${calendarId}/notify`),
  set: (calendarId: number, on: boolean) => api<NotifySettings>(`/api/calendars/${calendarId}/notify`, { method: 'PUT', body: { on } }),
  webpush: () => api<{ key: string; subscribed: boolean; reminders: boolean }>('/api/notify/webpush'),
  subscribe: (subscription: Subscription) => api<void>('/api/notify/webpush', { method: 'PUT', body: subscription }),
  unsubscribe: () => api<void>('/api/notify/webpush', { method: 'DELETE' }),
}

/** The message the service worker (`public/sw.js`) sends a window for a tapped notification: the address in `url`. */
export const OPEN_MESSAGE = 'nextime-open'

/** Only an address inside nextime: a path, never another site. */
export function insidePath(url: unknown): string | null {
  if (typeof url !== 'string' || !url.startsWith('/') || url.startsWith('//')) return null
  return url
}
