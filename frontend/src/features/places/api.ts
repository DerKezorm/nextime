/**
 * Places of events (TIME-8): address suggestions while typing and where a place lies, asked of nextime's server, which
 * asks a free OpenStreetMap service with the typed place only (AC-5); the small map is drawn by the server too. And the
 * way into the device's maps app, built here.
 */
import { api } from '../../api/client'
import { platform } from '../notify/browser'

export type Point = { lat: number; lon: number }
/** A suggestion: the address, below it the place's name where it has one; `value` is what goes into the field. */
export type Suggestion = { address: string; name: string; value: string; lat: number; lon: number }

export const placesApi = {
  suggest: (q: string) => api<{ suggestions: Suggestion[]; unavailable: boolean }>('/api/places', { query: { q } }),
  lookup: (q: string) => api<{ place: Point | null; unavailable: boolean }>('/api/places/lookup', { query: { q } }),
}

export function mapUrl(point: Point): string {
  return `/api/places/map?lat=${point.lat}&lon=${point.lon}`
}

/** A place that is a web address (a video call in the place field of a Google event, AC-7): a link, no map. */
export function isWebAddress(text: string): boolean {
  return /^https?:\/\/[^\s<>"]+$/i.test(text.trim())
}

/** A place worth a map without a point: a number in it and two words at least ("Lindenstraße 4", not "bei Oma"). */
export function looksLikeAddress(text: string): boolean {
  const words = text.split(/[\s,]+/).filter(Boolean)
  return /\d/.test(text) && words.length >= 2 && !/^[a-z][a-z0-9+.-]*:/i.test(text.trim())
}

/** The device's maps app (AC-3): Apple Maps on Apple devices, the system's choice on Android, Google Maps elsewhere.
 * The place goes in only as a search text, never as an address of its own (EC-7). */
export function mapsLink(text: string, point: Point | null, agent = typeof navigator === 'undefined' ? '' : navigator.userAgent): string {
  const query = encodeURIComponent(text.trim())
  const device = platform()
  if (device !== 'other') return point ? `https://maps.apple.com/?ll=${point.lat},${point.lon}&q=${query}` : `https://maps.apple.com/?q=${query}`
  if (/Android/i.test(agent)) return point ? `geo:${point.lat},${point.lon}?q=${query}` : `geo:0,0?q=${query}`
  return point ? `https://www.google.com/maps/search/?api=1&query=${point.lat}%2C${point.lon}` : `https://www.google.com/maps/search/?api=1&query=${query}`
}
