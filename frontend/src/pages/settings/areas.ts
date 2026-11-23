/**
 * The areas of the settings, each with its own address (`/settings/<slug>`), laid out as in nexnotes: "My account" for
 * every account, "Server" for the operator only, and "About nextime" (`/settings/about`) for everyone at the end of the
 * navigation, outside the groups. `/settings` is the profile; the addresses of before (`/account/<tab>`, `/server/<page>`)
 * lead to their area.
 *
 * A new area: add its slug to `AreaSlug`, an entry to `AREAS` (the place in the list is its place in the navigation),
 * its texts `settings.areas.<key>.title` and `.text` in both languages, and what it shows in `SettingsAreas.tsx`.
 */
import {
  ArrowLeftRight,
  Bell,
  CalendarDays,
  Cloud,
  HardDrive,
  Info,
  KeyRound,
  Mail,
  Palette,
  Plug,
  ScrollText,
  Share2,
  ShieldCheck,
  SlidersHorizontal,
  Smartphone,
  UserRound,
  Users,
  type LucideIcon,
} from 'lucide-react'

import type { Me } from '../../api/client'

type Role = Me['role']

export type AreaSlug =
  | 'profile'
  | 'security'
  | 'appearance'
  | 'calendars'
  | 'shares'
  | 'devices'
  | 'connections'
  | 'reminders'
  | 'move'
  | 'general'
  | 'sign-in'
  | 'mail'
  | 'storage'
  | 'google'
  | 'accounts'
  | 'log'
  | 'about'
export type AreaGroup = 'account' | 'server'

export type Area = { slug: AreaSlug; group: AreaGroup; icon: LucideIcon; key: string }

/** In the order of the navigation; `key` names the texts `settings.areas.<key>.title` and `.text`. */
export const AREAS: Area[] = [
  { slug: 'profile', group: 'account', icon: UserRound, key: 'profile' },
  { slug: 'security', group: 'account', icon: ShieldCheck, key: 'security' },
  { slug: 'appearance', group: 'account', icon: Palette, key: 'appearance' },
  { slug: 'calendars', group: 'account', icon: CalendarDays, key: 'calendars' },
  { slug: 'shares', group: 'account', icon: Share2, key: 'shares' },
  { slug: 'devices', group: 'account', icon: Smartphone, key: 'devices' },
  { slug: 'connections', group: 'account', icon: Plug, key: 'connections' },
  { slug: 'reminders', group: 'account', icon: Bell, key: 'reminders' },
  { slug: 'move', group: 'account', icon: ArrowLeftRight, key: 'move' },
  { slug: 'general', group: 'server', icon: SlidersHorizontal, key: 'general' },
  { slug: 'sign-in', group: 'server', icon: KeyRound, key: 'signIn' },
  { slug: 'mail', group: 'server', icon: Mail, key: 'mail' },
  { slug: 'storage', group: 'server', icon: HardDrive, key: 'storage' },
  { slug: 'google', group: 'server', icon: Cloud, key: 'google' },
  { slug: 'accounts', group: 'server', icon: Users, key: 'accounts' },
  { slug: 'log', group: 'server', icon: ScrollText, key: 'log' },
]

export const GROUPS: AreaGroup[] = ['account', 'server']

/** "About nextime": an area of every account, below the groups (its `group` only says who may open it). */
export const ABOUT_AREA: Area = { slug: 'about', group: 'account', icon: Info, key: 'about' }

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
  return { redirect: '/settings' }
}

/** The tabs of "My account" and the pages of "Server" before the areas: a saved link still leads to the place it meant. */
const FORMER_ACCOUNT: Record<string, AreaSlug> = {
  general: 'profile',
  calendars: 'calendars',
  shares: 'shares',
  devices: 'devices',
  connections: 'connections',
  reminders: 'reminders',
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
