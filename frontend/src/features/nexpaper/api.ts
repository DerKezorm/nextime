/**
 * The member's own nexpaper in the calendar (`backend/app/routers/nexpaper.py`): the connection and its open deadlines,
 * to look at only. The connection is held once for the page; each view asks for the days it shows.
 */
import { api } from '../../api/client'
import { onAccountChange } from '../../state/account'

export type NexpaperStatus = {
  connected: boolean
  /** The operator allows addresses in the own network ("Verbindungen ins eigene Netz"). */
  local_allowed: boolean
  url?: string
  host?: string
  name?: string
  version?: string
  /** "Meine Fristen" hidden like a calendar. */
  hidden?: boolean
  /** What went wrong last: a token run out, one that does not read, the API off, a nexpaper without deadlines. */
  problem?: string | null
  checked_at?: string | null
}

/** What is to be done by the day, as nexpaper names it. */
export type DeadlineWhat = 'pay' | 'cancel' | 'ends' | 'warranty' | 'follow_up'

export type Deadline = {
  /** `<document>-<kind>`: one document may have several deadlines. */
  id: string
  document_id: number
  kind: string
  what: DeadlineWhat
  title: string
  sender: string
  /** In cents, with its currency ("EUR"); `null` without an amount. */
  amount: number | null
  currency: string
  /** Only for a follow-up: what to do. */
  note: string
  /** The day to act by. */
  day: string
  status: 'inbox' | 'filed'
  /** The document in nexpaper. */
  link: string
}

export type DeadlineList = { connected: boolean; hidden: boolean; problem: string | null; deadlines: Deadline[] }

/** The row "Meine Fristen" shown alone, stored where an area of nextasks shown alone is. */
export const DEADLINES_ROW = 'nexpaper-deadlines'

export const nexpaperApi = {
  status: () => api<NexpaperStatus>('/api/nexpaper'),
  connect: (url: string, token: string) => api<NexpaperStatus>('/api/nexpaper', { method: 'PUT', body: { url, token } }),
  setHidden: (hidden: boolean) => api<NexpaperStatus>('/api/nexpaper', { method: 'PATCH', body: { hidden } }),
  disconnect: () => api<null>('/api/nexpaper', { method: 'DELETE' }),
  deadlines: (to: string, from?: string) => api<DeadlineList>('/api/deadlines', { query: { to, ...(from ? { from } : {}) } }),
}

// ---- The connection, once for the page ------------------------------------------------------------------------------

let status: NexpaperStatus | null = null
let asking: Promise<void> | null = null
const listeners = new Set<() => void>()

function tell(): void {
  for (const listener of [...listeners]) listener()
}

export function onNexpaper(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function nexpaperStatus(): NexpaperStatus | null {
  return status
}

export function setNexpaper(next: NexpaperStatus | null): void {
  status = next
  tell()
}

export function loadNexpaper(): Promise<void> {
  asking ??= nexpaperApi
    .status()
    .then((found) => setNexpaper(found && typeof found.connected === 'boolean' ? found : { connected: false, local_allowed: false }))
    .finally(() => {
      asking = null
    })
  return asking
}

/** Everything held of nexpaper on this page goes (another account, and the tests between each other). */
export function forgetNexpaper(): void {
  status = null
  asking = null
  tell()
}

onAccountChange(forgetNexpaper)
