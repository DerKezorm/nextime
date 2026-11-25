/**
 * The areas of the settings, each with its own address (`/settings/<slug>`, plan "Einstellungen"): "Mein Konto" for
 * every account, "Server" for the operator only, and "Über nextime" (`/settings/about`) for everyone, set apart at the
 * end. `/settings` alone is the list on a phone and the profile beside it on a wider page; the addresses of before
 * (`/account/<tab>`, `/server/<page>`, the area "Erinnerungen" `/settings/reminders`) lead to their area.
 *
 * A new area: add its slug to `AreaSlug`, an entry to `AREAS` (the place in the list is its place in the navigation),
 * for an area of nextime's own (`own`) its texts `settings.areas.<key>.title` and `.text` in both languages, and what it
 * shows in `SettingsAreas.tsx`.
 */
import { ArrowLeftRight, CalendarDays, Cloud, Share2 } from 'lucide-react'

import type { Me } from '../../api/client'
import type { IconLike } from '../../vendor/nexui'

type Role = Me['role']

export type AreaSlug =
  | 'profile'
  | 'security'
  | 'appearance'
  | 'calendars'
  | 'shares'
  | 'devices'
  | 'move'
  | 'notifications'
  | 'connections'
  | 'general'
  | 'sign-in'
  | 'mail'
  | 'storage'
  | 'google'
  | 'accounts'
  | 'log'
  | 'about'
export type AreaGroup = 'account' | 'server'

/** `own`: an area of nextime alone; its title and line are nextime's (`settings.areas.<key>.title`, `.text`), every
 * other area's come from the ui kit (`areaText`). */
export type Area = { slug: AreaSlug; group: AreaGroup; icon: IconLike; key: string; own?: boolean }

/** In the order of the navigation (plan "Einstellungen": Profile, Security, Appearance, nextime's own areas in its order,
 * Notifications, Connections; Server: General, Sign-in, Mail, Storage and backups, Set up Google, Accounts, Log). */
export const AREAS: Area[] = [
  { slug: 'profile', group: 'account', icon: 'UserRound', key: 'profile' },
  { slug: 'security', group: 'account', icon: 'ShieldCheck', key: 'security' },
  { slug: 'appearance', group: 'account', icon: 'Palette', key: 'appearance' },
  { slug: 'calendars', group: 'account', icon: CalendarDays, key: 'calendars', own: true },
  { slug: 'shares', group: 'account', icon: Share2, key: 'shares', own: true },
  { slug: 'devices', group: 'account', icon: 'Smartphone', key: 'devices', own: true },
  { slug: 'move', group: 'account', icon: ArrowLeftRight, key: 'move', own: true },
  { slug: 'notifications', group: 'account', icon: 'Bell', key: 'notifications' },
  { slug: 'connections', group: 'account', icon: 'Plug', key: 'connections' },
  { slug: 'general', group: 'server', icon: 'SlidersHorizontal', key: 'general' },
  { slug: 'sign-in', group: 'server', icon: 'KeyRound', key: 'signIn' },
  { slug: 'mail', group: 'server', icon: 'Mail', key: 'mail' },
  { slug: 'storage', group: 'server', icon: 'HardDrive', key: 'storage' },
  { slug: 'google', group: 'server', icon: Cloud, key: 'google', own: true },
  { slug: 'accounts', group: 'server', icon: 'Users', key: 'accounts' },
  { slug: 'log', group: 'server', icon: 'ScrollText', key: 'log' },
]

export const GROUPS: AreaGroup[] = ['account', 'server']

/** "About nextime": an area of every account, below the groups (its `group` only says who may open it). */
export const ABOUT_AREA: Area = { slug: 'about', group: 'account', icon: 'Info', key: 'about' }

/** The area of an address, the groups' and "About nextime". */
export function findArea(slug: AreaSlug): Area | undefined {
  return slug === ABOUT_AREA.slug ? ABOUT_AREA : AREAS.find((area) => area.slug === slug)
}

/** The areas an account may open: the operator all, a member those of its own account. */
export function areasFor(role: Role | undefined): Area[] {
  return role === 'operator' ? AREAS : AREAS.filter((area) => area.group === 'account')
}

export function areaPath(slug: AreaSlug): string {
  return `/settings/${slug}`
}

/** The answer of a provider after linking (`?linked=`, `?error=`): it belongs to Security, where the providers are. */
function providerAnswer(search: string): boolean {
  const params = new URLSearchParams(search)
  return params.has('linked') || params.has('error')
}

/**
 * Where an address of the settings leads: the area itself, or the address to go to instead (an area the account may not
 * open goes to the profile). The answer of a provider after linking comes to `/settings` and goes on to Security.
 */
export function resolveArea(asked: string | undefined, role: Role | undefined, search: string): { area: AreaSlug } | { redirect: string } {
  if (asked === undefined) return providerAnswer(search) ? { redirect: areaPath('security') + search } : { area: 'profile' }
  if (asked === ABOUT_AREA.slug || areasFor(role).some((area) => area.slug === asked)) return { area: asked as AreaSlug }
  const renamed = RENAMED[asked]
  if (renamed) return { redirect: areaPath(renamed) + search }
  return { redirect: '/settings' }
}

/** Areas that have another slug now: "Erinnerungen" is "Benachrichtigungen" in every nex app (plan, E1). */
const RENAMED: Record<string, AreaSlug> = { reminders: 'notifications' }

/** The tabs of "My account" and the pages of "Server" before the areas: a saved link still leads to the place it meant. */
const FORMER_ACCOUNT: Record<string, AreaSlug> = {
  general: 'profile',
  calendars: 'calendars',
  shares: 'shares',
  devices: 'devices',
  connections: 'connections',
  reminders: 'notifications',
}
const FORMER_SERVER: Record<string, AreaSlug> = { accounts: 'accounts', google: 'google', log: 'log' }

/**
 * The address of the settings for an address of before, with its query. `/account` itself is where the server sends
 * the browser back after linking a provider (`ACCOUNT_PAGE` in `backend/app/services/oidc_store.py`): that answer goes
 * to `/settings`, which hands it on to Security. An address of before that never existed goes to `/settings`.
 */
export function formerPath(pathname: string, search: string): string {
  const [, top, sub] = pathname.replace(/\/+$/, '').split('/')
  if (top === 'account') {
    if (sub === undefined) return providerAnswer(search) ? '/settings' + search : areaPath('profile') + search
    const slug = FORMER_ACCOUNT[sub]
    return slug ? areaPath(slug) + search : '/settings'
  }
  if (top === 'server') {
    if (sub === undefined) return areaPath('general') + search
    const slug = FORMER_SERVER[sub]
    return slug ? areaPath(slug) + search : '/settings'
  }
  return '/settings'
}
