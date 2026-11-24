/** The nextime app for iPhone and iPad in the browser (`backend/app/routers/app_pairing.py`, TIME-29), as nexnotes. */
import { api } from '../../api/client'

/** `ios` (iPhone, iPad), `ipados`, or `mac` (the iPad app on a Mac). */
export type DeviceKind = 'ios' | 'ipados' | 'mac'

export type AppDevice = {
  id: number
  name: string
  kind: DeviceKind
  created_at: string | null
  last_seen_at: string | null
  /** Notifications are on for it (TIME-29 AC-7, EC-8). */
  push: boolean
}

export type Pairing = {
  id: string
  secret: string
  expires_at: string
  /** `nextime://connect?server=…&pairing=…&secret=…`, drawn as the QR code. */
  link: string
  /** The address the app keeps; typed in without a camera. */
  address: string
  qr_svg: string
}

export type PairingState = { state: 'waiting' | 'used' | 'expired'; device: Omit<AppDevice, 'push'> | null }

export const appDevicesApi = {
  list: () => api<AppDevice[]>('/api/app-devices'),
  disconnect: (id: number) => api<void>(`/api/app-devices/${id}`, { method: 'DELETE' }),
  pair: () => api<Pairing>('/api/app-pairing', { method: 'POST', body: { address: window.location.origin } }),
  state: (id: string) => api<PairingState>(`/api/app-pairing/${encodeURIComponent(id)}`),
  withdraw: (id: string) => api<void>(`/api/app-pairing/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  connect: (state: string) => api<{ link: string }>('/api/app-connect', { method: 'POST', body: { state } }),
}

/** What the app sends along to `/app-connect`: 8 to 128 characters of base64url. */
export function validState(state: string | null): state is string {
  return state !== null && /^[A-Za-z0-9_-]{8,128}$/.test(state)
}

/** The app's address for "Abbrechen" on `/app-connect`. */
export function cancelLink(state: string): string {
  return `nextime://connected?error=cancelled&state=${encodeURIComponent(state)}`
}

/** The QR code as a picture: an image, never markup put into the page. */
export function qrSource(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}
