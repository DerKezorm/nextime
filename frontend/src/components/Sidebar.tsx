/**
 * The sidebar, after the accepted mock-up and laid out as nexnotes' (its drawing "Seitenleiste"): the mark at the top,
 * then "New event" and the field "Suchen" (TIME-22), the collection, the places Heute (the overview) and Kalender (the
 * view this device showed last; Day, Week, Month or Year are chosen in the switch above, asked for while testing
 * 2026-11-24), the list of
 * calendars by source (TIME-4) and, under a line, "Papierkorb" (TIME-23); at the bottom the account with the way to
 * the settings: picture, name and the gear, marked while the settings are open (TIME-2 AC-7). Signing out is in the
 * profile. Beside the page it is as wide as set on this device (`lib/columns.ts`, the handle at its edge); the drawer
 * on a phone keeps the mock-up's 260 px.
 */
import { Plus, Settings } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { NavLink, useLocation } from 'react-router-dom'

import { CalendarList } from '../features/calendars/CalendarList'
import { CollectionButton } from '../features/collections/CollectionMenu'
import { usePanel } from '../features/calendars/store'
import { SearchEntry } from '../features/search/SearchPage'
import { TrashEntry } from '../features/trash/TrashPage'
import { useOffline } from '../features/offline/connection'
import { CALENDAR_PATHS, useLastView } from '../features/views/state'
import { COLUMNS, useColumnWidth } from '../lib/columns'
import { useAuth } from '../state/auth'
import { Avatar } from './Avatar'
import { ColumnHandle } from './ColumnHandle'
import { Logo } from './Logo'

const ENTRY = 'flex items-center gap-2.5 rounded-[9px] px-2.5 py-2 text-sm transition-colors '
const QUIET = 'text-mist-300 hover:bg-ink-800 hover:text-mist-100'
/** A place in the list (a view), as nexnotes' "Alle Notizen" and its inbox. */
const PLACE = 'flex h-[34px] items-center rounded-lg px-2.5 text-[13.5px] transition-colors '

/** The views in the bar on a phone: there is no switch above on a phone, so Week and Month stand there themselves. */
// eslint-disable-next-line react-refresh/only-export-components
export const VIEWS = [
  { to: '/', key: 'nav.today', end: true },
  { to: '/week', key: 'nav.week', end: false },
  { to: '/month', key: 'nav.month', end: false },
] as const

/** `resizable`: beside the page, with the handle at its edge and the width of this device; never in the phone's drawer. */
export function Sidebar({ onNavigate, resizable = false }: { onNavigate?: () => void; resizable?: boolean }) {
  const { t } = useTranslation()
  const [setWidth] = useColumnWidth('sidebar')
  const width = resizable ? setWidth : COLUMNS.sidebar.fallback
  const path = useLocation().pathname
  const [lastView] = useLastView()
  return (
    <nav aria-label={t('app.mainMenu')} style={{ width }} className="relative flex h-full flex-col border-r border-ink-700 bg-ink-900">
      <div className="px-4 pt-4 pb-3">
        <NavLink to="/" onClick={onNavigate} aria-label={t('app.home')} className="inline-flex">
          <Logo size={28} withWordmark />
        </NavLink>
      </div>
      <div className="nt-scroll min-h-0 flex-1 overflow-y-auto px-2" data-testid="sidebar-places">
        <div className="pb-2">
          <NewEventButton onNavigate={onNavigate} />
        </div>
        <SearchEntry onNavigate={onNavigate} />
        <CollectionButton onNavigate={onNavigate} />
        <ul className="mt-1">
          <li>
            <NavLink to="/" end onClick={onNavigate} className={({ isActive }) => PLACE + (isActive ? 'nt-chosen font-semibold' : 'text-mist-100 hover:bg-ink-800')} data-testid="nav-today">
              {t('nav.today')}
            </NavLink>
          </li>
          <li>
            <NavLink to={lastView} onClick={onNavigate} className={() => PLACE + (CALENDAR_PATHS.includes(path) ? 'nt-chosen font-semibold' : 'text-mist-100 hover:bg-ink-800')} aria-current={CALENDAR_PATHS.includes(path) ? 'page' : undefined} data-testid="nav-calendar">
              {t('nav.calendar')}
            </NavLink>
          </li>
        </ul>
        <CalendarList onNavigate={onNavigate} />
        <div className="mt-3 border-t border-ink-700 pt-2 pb-2">
          <TrashEntry onNavigate={onNavigate} />
        </div>
      </div>
      <div className="border-t border-ink-700 p-2">
        <SettingsEntry onNavigate={onNavigate} />
      </div>
      {resizable && <ColumnHandle column="sidebar" />}
    </nav>
  )
}

/** "New event": the side panel with an empty event (TIME-5 AC-8). */
function NewEventButton({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation()
  const { open } = usePanel()
  // Without a connection nothing new (TIME-28 AC-3).
  const offline = useOffline()
  return (
    <button
      type="button"
      className="nt-btn nt-btn-accent w-full"
      disabled={offline}
      aria-label={offline ? t('offline.plusOff') : undefined}
      onClick={() => {
        onNavigate?.()
        open({ kind: 'new' })
      }}
      data-testid="new-event"
    >
      <Plus className="h-4 w-4" strokeWidth={2.4} aria-hidden />
      {t('nav.newEvent')}
    </button>
  )
}

/** The account at the bottom of the sidebar: the way to the settings. */
function SettingsEntry({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation()
  const { me } = useAuth()
  const shown = me?.display_name || me?.name || ''
  return (
    <NavLink
      to="/settings"
      onClick={onNavigate}
      className={({ isActive }) => ENTRY + (isActive ? 'nt-chosen' : QUIET)}
      aria-label={`${shown} · ${t('nav.settings')}`}
      title={`${shown} · ${t('nav.settings')}`}
      data-testid="settings-entry"
    >
      <Avatar accountId={me?.id ?? 0} color={me?.color} name={shown} stamp={me?.avatar} />
      <span className="min-w-0 flex-1 truncate">{shown}</span>
      <Settings className="h-4 w-4 shrink-0" strokeWidth={1.8} aria-hidden />
    </NavLink>
  )
}
