/** Keys for programs and AI tools over the server (`backend/app/routers/keys.py`, TIME-26). */
import { api } from '../../api/client'

export type KeyLevel = 'read' | 'write'

export type KeyEntry = {
  id: number
  name: string
  level: KeyLevel
  mcp: boolean
  /** The calendars it was made for, with their names; null for all (later ones too). */
  calendars: { id: number; name: string }[] | null
  prefix: string
  created_at: string | null
  expires_at: string | null
  expired: boolean
  last_used_at: string | null
}

export type KeyList = {
  /** The operator's switches "Programmschnittstelle (API) für Mitglieder" and "MCP-Zugang" (AC-5). */
  api_allowed: boolean
  mcp_allowed: boolean
  /** The address programs are given. */
  address: string
  max_keys: number
  expiry_days: (number | null)[]
  default_days: number
  keys: KeyEntry[]
}

export type NewKey = { name: string; level: KeyLevel; mcp: boolean; calendar_ids: number[] | null; days: number | null; client_ref: string }
export type MadeKey = { made: KeyEntry; token: string | null }
export type KeyOverview = { accounts: { account: { id: number; name: string }; keys: number; mcp: number }[]; keys: number; mcp: number }

/** The choices of "Ablauf" (AC-11): 90 days chosen from the start, null is "nie". */
export const EXPIRY_CHOICES: (number | null)[] = [30, 90, 365, null]
export const DEFAULT_DAYS = 90

export const keysApi = {
  list: () => api<KeyList>('/api/keys'),
  make: (body: NewKey) => api<MadeKey>('/api/keys', { method: 'POST', body }),
  revoke: (id: number) => api<void>(`/api/keys/${id}`, { method: 'DELETE' }),
  overview: () => api<KeyOverview>('/api/admin/keys'),
}
