/**
 * The server's API. Every call names this browser tab in `X-Nextime-Client` (changes without it are refused, the
 * wall against requests from other sites), and the device's time zone in `X-Nextime-Zone`: the first one the server
 * hears for an account becomes its home zone (TIME-3 AC-11). Errors come back as `ApiError` with the server's code; the page builds
 * its sentence from `errors.<code>` in the language files.
 */

import type { Readiness } from '../vendor/nexkonten/konten'
import type { AuthentikAddress } from '../vendor/nexoidc/oidc'

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    public readonly values: Record<string, unknown> = {},
  ) {
    super(code)
  }
}

const CLIENT_KEY = 'nextime.client'
/** Sent on `window` when a request finds that the session is gone: the page goes back to the sign-in. */
export const SIGNED_OUT_EVENT = 'nextime:signed-out'

function randomId(): string {
  const bytes = new Uint8Array(12)
  crypto.getRandomValues(bytes)
  return 'tab-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

let clientId: string | null = null

/** The device's time zone as an IANA name ("Europe/Berlin"); empty when the browser does not say. */
export function deviceZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || ''
  } catch {
    return ''
  }
}

function zoneHeader(): Record<string, string> {
  const zone = deviceZone()
  return zone ? { 'X-Nextime-Zone': zone } : {}
}

/** One id per tab: sessionStorage survives a reload of the tab but is not shared with other tabs. */
/** The way of the shared modules' pages that call `fetch` themselves (updates, wasneu): every request names its tab
 * (changes are refused without it). */
export function tabFetch(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Nextime-Client', tabId())
  return fetch(path, { credentials: 'same-origin', ...init, headers })
}

export function tabId(): string {
  if (clientId) return clientId
  try {
    clientId = sessionStorage.getItem(CLIENT_KEY)
    if (!clientId) {
      clientId = randomId()
      sessionStorage.setItem(CLIENT_KEY, clientId)
    }
  } catch {
    clientId = randomId()
  }
  return clientId
}

type Options = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  query?: Record<string, string | number | boolean | string[] | undefined>
  body?: unknown
  /** A file as the body (an upload), sent as it is. */
  raw?: Blob
  /** The answer is a file, not JSON. */
  blob?: boolean
  /** Headers of its own, next to the ones every request carries (the password of an upload). */
  headers?: Record<string, string>
  /** Stops the request (a view that asks for another window meanwhile, TIME-28). */
  signal?: AbortSignal
}

const BUSY_TRIES = 3
const BUSY_WAIT_MS = 1500

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const repeatable = (options.method ?? 'GET') === 'GET'
  for (let attempt = 1; ; attempt++) {
    try {
      return await once<T>(path, options)
    } catch (error) {
      if (!(error instanceof ApiError && error.code === 'busy' && repeatable && attempt < BUSY_TRIES)) throw error
      await new Promise((resolve) => setTimeout(resolve, BUSY_WAIT_MS))
    }
  }
}

async function once<T>(path: string, options: Options): Promise<T> {
  const url = new URL(path, window.location.origin)
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (Array.isArray(value)) for (const item of value) url.searchParams.append(key, item)
    else if (value !== undefined) url.searchParams.set(key, String(value))
  }
  const headers: Record<string, string> = { ...options.headers, ...zoneHeader(), Accept: 'application/json', 'X-Nextime-Client': tabId() }
  let body: BodyInit | undefined
  if (options.raw) body = options.raw
  else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  const response = await fetch(url.pathname + url.search, { method: options.method ?? 'GET', headers, body, ...(options.signal ? { signal: options.signal } : {}) })
  if (response.status === 204) return undefined as T
  if (options.blob && response.ok) return (await response.blob()) as T
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail
    if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
      const { code, message: _message, ...values } = detail
      if (response.status === 401 && (code === 'sign_in_required' || code === 'account_blocked')) window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: { code } }))
      throw new ApiError(response.status, code, values)
    }
    throw new ApiError(response.status, response.status === 401 ? 'sign_in_required' : 'internal_error')
  }
  return data as T
}

/** An upload with progress (fetch cannot report it): resolves with the server's answer. */
export function upload<T>(path: string, query: Record<string, string | number>, file: Blob, onProgress?: (part: number) => void): Promise<T> {
  const url = new URL(path, window.location.origin)
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, String(value))
  return new Promise<T>((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', url.pathname + url.search)
    request.setRequestHeader('X-Nextime-Client', tabId())
    for (const [name, value] of Object.entries(zoneHeader())) request.setRequestHeader(name, value)
    request.setRequestHeader('Accept', 'application/json')
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total)
    }
    request.onload = () => {
      let data: unknown = null
      try {
        data = JSON.parse(request.responseText)
      } catch {
        // no JSON
      }
      if (request.status >= 200 && request.status < 300) return resolve(data as T)
      const detail = (data as { detail?: { code?: string } } | null)?.detail
      reject(new ApiError(request.status, typeof detail?.code === 'string' ? detail.code : 'internal_error', (detail as Record<string, unknown>) ?? {}))
    }
    request.onerror = () => reject(new ApiError(0, 'network'))
    request.send(file)
  })
}

// ---- Types ----------------------------------------------------------------------------------------------------------

export type Me = {
  id: number
  name: string
  display_name: string
  role: 'operator' | 'member'
  sign_in: 'password' | 'oidc'
  email: string
  /** The address a sign-in provider knows the account by, offered when it differs from the own one. */
  provider_email?: string
  /** The provider whose address is offered, as on its button (konten 0.3.4). */
  provider_email_from?: string
  /** Empty: the browser's language. Chosen once, it counts on every device of the account. */
  language: string
  /** First day of the week, week numbers, clock, time zone and home zone (TIME-3), on every device. */
  display?: Display
  /** The color of the circle with the initial (TIME-2 EC-11). */
  color?: number | null
  /** When the profile picture was set (TIME-2 AC-5); none without one. */
  avatar?: string | null
  two_factor: boolean
  two_factor_recovery_left: number
  /** The module's state of the second factor (`factor.state`): none, required, active, passkey. */
  factor_state?: string
  /** NEXTIME_SECOND_FACTOR_OFF holds: no second factor is asked of anybody. */
  second_factor_off?: boolean
  password_changed_at?: string | null
  created_at?: string
  last_seen_at?: string | null
  version: string
  whats_new_seen: string
  mail?: boolean
  second_factor_setup_required?: boolean
  min_password?: number
  /** The password was set through the operator's reset link; the page says so until it is put away. */
  password_reset_notice?: boolean
  /** The address counts (signing in, a forgotten password); a new one waiting for its link; the name before. */
  email_confirmed?: boolean
  pending_email?: string
  former_name?: string
  /** A mail when a browser signs in for the first time (on from the start). */
  new_sign_in_mail?: boolean
  /** A password set through a link: the page lists what still has access, until put away. */
  new_password_origin?: string
  /** The address proved by a link (konten 1.1.0): an address of before counts as proven (E23). */
  email_proven?: boolean
  /** A mail server and the public address: "Address not confirmed yet" offers a link only then (konten 1.1.0). */
  mail_server?: boolean
  /** Light, dark or as the system, at the account on every device; "" chose nothing yet (konten 1.1.0). */
  theme?: string
  /** konten 1.1.0 (E49): whether "Passkey hinzufügen" is offered here (a public https address, or localhost). */
  passkeys_can_add?: boolean
}

/** How the account sees its times (TIME-3). `week_start`: 0 Sunday … 6 Saturday. `clock`: `auto` follows the
 * language. `time_zone`: empty follows the device. `home_zone`: the device's zone at the first sign-in. */
export type Display = {
  week_start: number
  week_numbers: boolean
  clock: 'auto' | '24' | '12'
  time_zone: string
  home_zone: string
  /** The same event in two calendars of one person shows once (TIME-14 AC-4); missing counts as on. */
  merge_duplicates?: boolean
  /** Events in the month with a dot in their calendar's color, or tinted in it; missing counts as the dot. */
  month_style?: 'dot' | 'tint'
}

export type SetupState = {
  needs_setup: boolean
  code_required: boolean
  signed_in: boolean
  version: string
  min_password: number
  /** The data folder lies on a network share: the setup page warns. */
  data_on_share?: boolean
  /** Free space under the data folder, while nobody has set nextime up. */
  data_free?: number | null
  /** The data folder as the server sees it; only while nextime is not set up. */
  data_dir?: string | null
  /** Reached over HTTPS; only while nextime is not set up (TIME-1 AC-6). */
  https?: boolean | null
}

/** A button of the sign-in page: an active provider (the shared sign-in module, vendor/nexoidc). */
export type ProviderButton = { slug: string; label: string }
export type Methods = {
  password: boolean
  providers: ProviderButton[]
  /** "Sign in with a passkey" (the operator's switch). */
  passkeys?: boolean
  /** "Forgot your password?" by mail (a mail server, the public address, signing in by password). */
  forgot_mail?: boolean
}
/** An active provider and whether the own account is linked to it. */
/** The operator's dialog "Reset the second factor": what the account has. */
export type FactorResetOverview = {
  authenticator: boolean
  recovery_codes: number
  passkeys: { uid: string; name: string }[]
  devices: { uid: string; label: string }[]
}
export type MyProvider = ProviderButton & { linked: boolean; managed: string }
/** A provider an account is linked to, as a mark in the operator's account list. */
export type LinkedProvider = { id: number; slug: string; label: string; managed: string }

/** An account in the operator's list: name, address, way of signing in, role, storage and last use; never content. */
export type AccountRow = {
  id: number
  /** When the profile picture was set (TIME-2 AC-5). */
  avatar?: string | null
  /** The color of the circle with the initial (TIME-2 EC-11). */
  color?: number | null
  name: string
  display_name: string
  email: string
  role: 'operator' | 'member'
  sign_in: 'password' | 'oidc'
  language: string
  two_factor: boolean
  /** Passkeys of the account (a second factor too, since konten 0.3.2). */
  passkeys?: number
  created_at: string
  last_seen_at: string | null
  locked: boolean
  blocked: boolean
  blocked_at: string | null
  deleted_at: string | null
  purge_at: string | null
  has_password: boolean
  /** The address is held by another account too (left by an older version): it signs nobody in. */
  email_duplicate?: boolean
  /** The address counts (signing in, a forgotten password); left out by an older server. */
  email_confirmed?: boolean
  providers: LinkedProvider[]
  storage_used: number
  storage_limit: number | null
}

export type Invite = {
  id: number
  role: 'operator' | 'member'
  email: string
  display_name?: string
  by: string | null
  created_at: string
  expires_at: string
  /** It ran out; it stays in the list until withdrawn (konten 0.3.9). */
  expired?: boolean
}

export type InviteOffer = {
  by: string | null
  email: string
  /** The fields as the page fills them: the invitation's display name, a username suggested from it. */
  display_name?: string
  name?: string
  role: 'operator' | 'member'
  min_password: number
  password: boolean
  signed_in_as: string | null
}

export type ServerSettings = {
  public_url: string
  password_login: boolean
  two_factor_required: boolean
  backup_schedule: 'off' | 'daily'
  backup_time: string
  backup_keep: number
  smtp_host: string
  smtp_port: number
  smtp_security: 'starttls' | 'tls' | 'none'
  smtp_user: string
  smtp_password_set: boolean
  smtp_from: string
  /** The name system mail goes out under ("" for nextime), and the last result (the shared post module). */
  smtp_sender_name?: string
  mail_last_ok_at?: string
  mail_last_error?: string
  mail_last_error_at?: string
  /** Passkeys may be added and used to sign in (on from the start). */
  passkeys_allowed?: boolean
  upload_max_mb: number
  trash_days: number
  share_links: boolean
  /** Google is offered: set up and switched on (TIME-1 AC-5). */
  google_enabled: boolean
  google_configured: boolean
  api_enabled: boolean
  mcp_enabled: boolean
  /** Members may connect their nextasks at an address in the own network (TIME-25, as nexnotes). */
  connections_local_network: boolean
  address_suggestions: boolean
  update_check: boolean
  data_total: number
  data_used: number
  data_free: number
  /** What nextime itself takes in the data folder, and its parts. */
  nextime_used?: number
  nextime_parts?: Partial<Record<'database' | 'attachments' | 'backups' | 'other', number>>
  data_on_share: boolean
  proxy_unknown?: boolean
}

export type SettingsChange = Partial<
  Omit<ServerSettings, 'smtp_password_set' | 'google_configured' | 'data_total' | 'data_used' | 'data_free' | 'nextime_used' | 'nextime_parts' | 'data_on_share' | 'proxy_unknown'>
> & {
  smtp_password?: string
  confirm_address?: boolean
}

export type BackupEntry = {
  name: string
  size: number
  created: string
  kind: 'manual' | 'scheduled' | 'update'
  note: string
  events: number
  files: number
  version: string
  uploaded: boolean
}

/** The box "Backups" (TIME-1 AC-8): rhythm in the server's zone, the last one, how many are kept, a failure. */
export type BackupSummary = {
  schedule: 'off' | 'daily'
  time: string
  zone: string
  keep: number
  kept: number
  count: number
  last: { created: string; size: number } | null
  failure: { at: string; reason: string } | null
  running: boolean
}

export type BackupBrief = {
  name: string
  version: string
  created: string
  kind: string
  events: number
  files: number
  database_ok: boolean
  files_ok: boolean
  damaged: string[]
  would_add: number
  would_change: number
  would_remove: number
  /** Events and accounts a restore brings back, changes and takes away: numbers only. */
  events_add: number
  events_change: number
  events_remove: number
  accounts_add: number
  accounts_remove: number
  usable: boolean
}

/** The box "State" and the version (TIME-1 AC-7, AC-9). */
export type ServerState = { version: string; newer: string | null; https: boolean; internet: boolean; zone: string; update_check?: boolean }


/** An entry of the provider list as the operator sees it: never the secret, only whether one is stored. */
export type OidcProvider = {
  id: number
  slug: string
  label: string
  issuer: string
  client_id: string
  has_secret: boolean
  scopes: string
  enabled: boolean
  auto_create: boolean
  trusts_second_factor: boolean
  /** "" by hand, "authentik" through the button, anything else counts as by hand. */
  managed: string
  position: number
  redirect_uri: string
  links: number
  editable: boolean
}
/** The provider form. `slug` counts only when adding; an empty `client_secret` keeps the stored one. */
export type OidcProviderForm = {
  label: string
  slug?: string
  issuer: string
  client_id: string
  client_secret: string
  scopes: string
  enabled: boolean
  auto_create: boolean
  trusts_second_factor: boolean
}
export type OidcImpact = { issuer_change: number; count: number; only: number; only_names: string[] }
export type AuthentikStep = { key: string; ok: boolean; detail: string; reason?: string; status?: number }
export type AuthentikResult = {
  ok: boolean
  steps: AuthentikStep[]
  client_id: string
  issuer: string
  provider_id: number | null
  links_dropped: number
  /** With a step stopped on `issuer_change`: how many accounts would have to link again. */
  count?: number
  /** The public address this run stored (the one the operator confirmed), '' when nextime had one. */
  public_url?: string
  /** With a step stopped on `public_url_needed`: what the card shows about the address. */
  address?: AuthentikAddress | null
}
/** What saving the public address would leave behind at authentik (409 `public_url_change`, module `public_url_change`). */
export type AddressQuestion = { count: number; hand: number; address: string }

// ---- Calls ----------------------------------------------------------------------------------------------------------

export const authApi = {
  setupState: () => api<SetupState>('/api/setup'),
  setup: (body: { display_name: string; name: string; email: string; password: string; password_confirm: string; code: string; language: string; update_check: boolean }) =>
    api<Me>('/api/setup', { method: 'POST', body }),
  methods: () => api<Methods>('/api/auth/methods'),
  login: (name: string, password: string, remember = true) =>
    api<Me | { second_factor: true; passkey?: boolean; code?: boolean }>('/api/auth/login', { method: 'POST', body: { name, password, remember } }),
  /** "Forgot your password?": the same answer whatever was typed. */
  forgot: (name: string) => api<{ sent: boolean }>('/api/auth/forgot', { method: 'POST', body: { name } }),
  code: (code: string) => api<Me>('/api/auth/login/totp', { method: 'POST', body: { code } }),
  cancelCode: () => api<void>('/api/auth/login/totp/cancel', { method: 'POST' }),
  logout: () => api<void>('/api/auth/logout', { method: 'POST' }),
  logoutEverywhere: () => api<void>('/api/auth/logout-all', { method: 'POST' }),
  me: () => api<Me>('/api/auth/me'),
  language: (language: string) => api<Me>('/api/me/language', { method: 'PUT', body: { language } }),
  /** Light, dark or as the system, for the account on every device (konten 1.1.0). */
  theme: (theme: string) => api<Me>('/api/me/theme', { method: 'PUT', body: { theme } }),
  /** The first load after konten 1.1.0: the browser's choice of before, once, while the account has none. */
  adoptTheme: (theme: string) => api<{ theme: string }>('/api/me/theme/adopt', { method: 'POST', body: { theme } }),
  /** "Address not confirmed yet" › "Send link" (konten 1.1.0). */
  proveEmail: () => api<Me>('/api/me/email/prove', { method: 'POST' }),
  display: (change: Partial<Omit<Display, 'home_zone'>>) => api<Me>('/api/me/display', { method: 'PUT', body: change }),
  avatar: (picture: Blob) => api<Me>('/api/auth/avatar', { method: 'PUT', raw: picture }),
  removeAvatar: () => api<Me>('/api/auth/avatar', { method: 'DELETE' }),
  profile: (change: { display_name?: string; email?: string; name?: string; password?: string }) =>
    api<Me>('/api/me/profile', { method: 'PUT', body: change }),
  resendEmail: () => api<Me>('/api/me/email/resend', { method: 'POST' }),
  withdrawEmail: () => api<Me>('/api/me/email/pending', { method: 'DELETE' }),
  newSignInMail: (on: boolean) => api<Me>('/api/me/new-sign-in-mail', { method: 'PUT', body: { on } }),
  sessions: () => api<SessionEntry[]>('/api/auth/sessions'),
  endSession: (uid: string) => api<void>(`/api/auth/sessions/${encodeURIComponent(uid)}`, { method: 'DELETE' }),
  access: () => api<{ origin: string; access: AccessEntry[] }>('/api/auth/access'),
  disconnectAll: () => api<{ disconnected: number }>('/api/auth/access/disconnect', { method: 'POST' }),
  accessSeen: () => api<Me>('/api/auth/access/seen', { method: 'POST' }),
  password: (current: string, next: string, confirm: string) =>
    api<void>('/api/auth/password', { method: 'PUT', body: { current, new: next, password_confirm: confirm } }),
  resetNoticeSeen: () => api<Me>('/api/me/password-reset-notice/seen', { method: 'POST' }),
  myProviders: () => api<MyProvider[]>('/api/oidc/me'),
  linkStart: (slug: string, password: string) =>
    api<{ url: string }>(`/api/oidc/${encodeURIComponent(slug)}/link`, { method: 'POST', body: { password } }),
  unlink: (slug: string) => api<void>(`/api/oidc/${encodeURIComponent(slug)}/link`, { method: 'DELETE' }),
  /** Takes the address a provider knows the account by, or stops the page from offering it. */
  takeProviderEmail: (password = '') => api<Me>('/api/oidc/me/address', { method: 'POST', body: { password } }),
  declineProviderEmail: () => api<void>('/api/oidc/me/address', { method: 'DELETE' }),
  /** The browser's clock, so the log can warn when the server's is far off. */
  clock: () => api<{ now: number; drift: number }>('/api/health/clock', { method: 'POST', body: { now: Date.now() } }),
}

/** A browser or a paired device in "Where you are signed in". */
export type SessionEntry = {
  uid: string
  kind: string
  label: string
  address: string
  created_at: string
  last_seen_at: string | null
  current: boolean
  device: boolean
}

/** What still has access after a password set through a link: devices, API tokens, passkeys. */
export type AccessEntry = { id: string; kind: string; label: string; last_used_at: string | null }

/** A passkey of the own account. */
export type PasskeyEntry = { uid: string; name: string; created_at: string; last_used_at: string | null }

/** A confirmation link for a new address, opened without an account. */
export const emailLinkApi = {
  offer: (token: string) => api<{ email: string }>(`/api/email/${encodeURIComponent(token)}`),
  confirm: (token: string) => api<{ email: string }>(`/api/email/${encodeURIComponent(token)}`, { method: 'POST' }),
}

/** Passkeys: sign in with one, add and remove the own (the browser's WebAuthn in `lib/passkeys.ts`). */
export const passkeysApi = {
  list: () => api<PasskeyEntry[]>('/api/auth/passkeys'),
  beginAdd: (password: string) =>
    api<{ token: string; options: Record<string, unknown> }>('/api/auth/passkeys/begin', { method: 'POST', body: { password } }),
  finishAdd: (token: string, credential: unknown, name: string) =>
    api<PasskeyEntry>('/api/auth/passkeys/finish', { method: 'POST', body: { token, credential, name } }),
  remove: (uid: string, password: string) =>
    api<Me>(`/api/auth/passkeys/${encodeURIComponent(uid)}`, { method: 'DELETE', body: { password } }),
  beginSignIn: () => api<{ token: string; options: Record<string, unknown> }>('/api/auth/passkey/begin', { method: 'POST' }),
  finishSignIn: (token: string, credential: unknown) =>
    api<Me>('/api/auth/passkey/finish', { method: 'POST', body: { token, credential } }),
}

/** A reset link from the operator, opened without an account. */
export const resetApi = {
  offer: (token: string) =>
    api<{ name: string; username?: string; min_password: number; expires_at: string }>(`/api/reset/${encodeURIComponent(token)}`),
  use: (token: string, password: string, confirm: string) =>
    api<void>(`/api/reset/${encodeURIComponent(token)}`, { method: 'POST', body: { password, password_confirm: confirm } }),
}

export const invitesApi = {
  offer: (token: string) => api<InviteOffer>(`/api/invite/${encodeURIComponent(token)}`),
  accept: (token: string, body: { display_name: string; name: string; email: string; password: string; password_confirm: string }) =>
    api<Me>(`/api/invite/${encodeURIComponent(token)}`, { method: 'POST', body }),
  list: () => api<Invite[]>('/api/invites'),
  make: (body: { days: number; email: string; display_name: string; role: 'operator' | 'member'; send: boolean; language: string }) =>
    api<Invite & { link: string; sent: boolean }>('/api/invites', { method: 'POST', body }),
  withdraw: (id: number) => api<void>(`/api/invites/${id}`, { method: 'DELETE' }),
}

/** The operator's list of accounts. Acts a stolen session must not be enough for carry the own password again. */
export const accountsApi = {
  list: () => api<AccountRow[]>('/api/accounts'),
  impact: (id: number) => api<{ calendars: number; events: number; shared: number; days: number }>(`/api/accounts/${id}/impact`),
  remove: (id: number, current_password: string) => api<void>(`/api/accounts/${id}`, { method: 'DELETE', body: { current_password } }),
  restore: (id: number, current_password: string) =>
    api<AccountRow>(`/api/accounts/${id}/restore`, { method: 'POST', body: { current_password } }),
  block: (id: number, current_password: string) => api<void>(`/api/accounts/${id}/block`, { method: 'POST', body: { current_password } }),
  unblock: (id: number, current_password: string) => api<void>(`/api/accounts/${id}/unblock`, { method: 'POST', body: { current_password } }),
  role: (id: number, role: 'operator' | 'member', current_password: string) =>
    api<AccountRow>(`/api/accounts/${id}/role`, { method: 'PUT', body: { role, current_password } }),
  limit: (id: number, limit: number | null, current_password: string) =>
    api<AccountRow>(`/api/accounts/${id}/storage-limit`, { method: 'PUT', body: { limit, current_password } }),
  /** A one-time link with which the person sets a new password themselves; the operator never knows it. */
  resetLink: (id: number, days: number, current_password: string) =>
    api<{ link: string | null; sent?: boolean; expires_at: string }>(`/api/accounts/${id}/reset-link`, { method: 'POST', body: { days, current_password } }),
  /** Another username or display name; the address only taken away, never set (the operator's own password). */
  rename: (id: number, name: string, current_password: string) =>
    api<AccountRow>(`/api/accounts/${id}/name`, { method: 'PUT', body: { name, current_password } }),
  displayName: (id: number, display_name: string, current_password: string) =>
    api<AccountRow>(`/api/accounts/${id}/display-name`, { method: 'PUT', body: { display_name, current_password } }),
  removeEmail: (id: number, current_password: string) =>
    api<AccountRow>(`/api/accounts/${id}/email`, { method: 'DELETE', body: { current_password } }),
  signOut: (id: number) => api<void>(`/api/accounts/${id}/sign-out`, { method: 'POST' }),
  /** What a reset of the second factor would take away (the shared module's ``factor.reset_overview``). */
  resetOverview: (id: number) => api<FactorResetOverview>(`/api/accounts/${id}/totp/reset`),
  resetSecondFactor: (id: number, current_password: string, disconnect_devices = true, remove_passkeys = true) =>
    api<void>(`/api/accounts/${id}/totp/reset`, {
      method: 'POST',
      body: { current_password, disconnect_devices, remove_passkeys },
    }),
}

/** Server › Sign-in (konten 1.1.0): the switches with what may change, the public address, whether the operator has a
 * password. */
export type SignInSettingsView = {
  password_login: boolean
  factor_required: boolean
  passkeys_allowed: boolean
  password_may_go_off: boolean
  factor_may_go_on: boolean
  second_factor_off?: boolean
  public_url: string
  operator_has_password: boolean
}

export const signInApi = {
  read: () => api<SignInSettingsView>('/api/server/sign-in'),
  save: (change: { password_login?: boolean; factor_required?: boolean; passkeys_allowed?: boolean }, current_password = '') =>
    api<SignInSettingsView>('/api/server/sign-in', { method: 'PUT', body: { ...change, current_password } }),
  readiness: () => api<Readiness>('/api/server/sign-in/readiness'),
}

export const settingsApi = {
  read: () => api<ServerSettings>('/api/settings'),
  save: (change: SettingsChange) => api<ServerSettings>('/api/settings', { method: 'PUT', body: change }),
  mailTest: (to: string, language: string) => api<void>('/api/settings/mail-test', { method: 'POST', body: { to, language } }),
}

export const serverApi = {
  state: () => api<ServerState>('/api/server/state'),
}

export const backupsApi = {
  list: () => api<BackupEntry[]>('/api/backups'),
  summary: () => api<BackupSummary>('/api/backups/summary'),
  make: (note = '') => api<{ name: string }>('/api/backups', { method: 'POST', body: { note } }),
  check: (name: string) => api<BackupBrief>(`/api/backups/${encodeURIComponent(name)}/check`, { method: 'POST' }),
  restore: (name: string, password: string) =>
    api<BackupBrief & { restarting: boolean }>(`/api/backups/${encodeURIComponent(name)}/restore`, { method: 'POST', body: { password } }),
  remove: (name: string, password: string) => api<void>(`/api/backups/${encodeURIComponent(name)}`, { method: 'DELETE', body: { password } }),
  download: (name: string, password: string) =>
    api<Blob>(`/api/backups/${encodeURIComponent(name)}/download`, { method: 'POST', body: { password }, blob: true }),
}


/** The operator's sign-in providers (Settings, Server, Sign-in) and the authentik button. */
export const oidcAdminApi = {
  providers: () => api<OidcProvider[]>('/api/oidc/admin/providers'),
  addProvider: (form: OidcProviderForm) => api<OidcProvider>('/api/oidc/admin/providers', { method: 'POST', body: form }),
  saveProvider: (id: number, form: OidcProviderForm) =>
    api<OidcProvider & { dropped: number }>(`/api/oidc/admin/providers/${id}`, { method: 'PUT', body: form }),
  removeProvider: (id: number) => api<OidcImpact>(`/api/oidc/admin/providers/${id}`, { method: 'DELETE' }),
  providerImpact: (id: number, issuer = '') =>
    api<OidcImpact>(`/api/oidc/admin/providers/${id}/impact${issuer ? `?issuer=${encodeURIComponent(issuer)}` : ''}`),
  orderProviders: (ids: number[]) => api<ProviderButton[]>('/api/oidc/admin/providers/order', { method: 'PUT', body: { ids } }),
  authentik: (url: string, token: string, confirm_issuer_change = false, public_url = '') =>
    api<AuthentikResult>('/api/oidc/authentik/setup', { method: 'POST', body: { url, token, confirm_issuer_change, public_url } }),
  /** What the authentik card shows about the public address. */
  authentikAddress: () => api<AuthentikAddress>('/api/oidc/authentik/address'),
  /** "Use it only": saves the address; 409 `public_url_change` asks first (send again with `confirm`). */
  authentikSaveAddress: (public_url: string, confirm = false) =>
    api<AuthentikAddress>('/api/oidc/authentik/address', { method: 'POST', body: { public_url, confirm } }),
  /** Takes an account's link to a provider, with the operator's own password. */
  unlinkAccount: (accountId: number, providerId: number, current_password: string) =>
    api<void>(`/api/oidc/admin/accounts/${accountId}/links/${providerId}`, { method: 'DELETE', body: { current_password } }),
}

/** The password for an upload rides in a header, as base64 of its UTF-8: a header carries no umlauts. */
export function passwordHeader(password: string): string {
  let bytes = ''
  for (const byte of new TextEncoder().encode(password)) bytes += String.fromCharCode(byte)
  return btoa(bytes)
}
