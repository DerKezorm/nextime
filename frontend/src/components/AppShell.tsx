/**
 * The frame of the signed-in app, after the accepted mock-up: the sidebar on the left; below 1024 px (a tablet upright,
 * a phone) a slim head with the mark, the collection, the invitations and the profile (the sidebar's lists behind it,
 * TIME-28 AC-6, AC-8), and a bar at the bottom with Heute, Woche, the round plus for a new event, Monat and Suchen
 * (TIME-28 AC-5, EC-10). Without a connection the orange hint stands over the page and everything that changes
 * something is off (TIME-28 AC-3); a new version of the installed app offers itself as a bar (EC-7), and after an update "What's new" below it. Over the page the hint
 * while travelling ("Times in New York (UTC−5)", TIME-3 AC-5). A session that ends while the page is open shows its
 * dialog over everything, so nothing typed is lost. Collections (TIME-13) switch in the sidebar and, on a phone, in the
 * head; while one calendar is shown alone a bar says so. The keys T, W, M, H, the arrows, K, G and N work everywhere in the
 * frame (TIME-12 AC-6). Over the views the hints about connected sources (TIME-15 EC-5, EC-6), at the bottom short
 * messages. After the password was set through the operator's reset link, the
 * account hears so once it is signed in.
 */
import { Plus, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { authApi } from '../api/client'
import { travelHint } from '../lib/display'
import { useDisplay } from '../lib/useDisplay'
import { EventPanelHost } from '../features/calendars/EventPanel'
import { LiveRefresh, PanelProvider, usePanel } from '../features/calendars/store'
import { CollectionButton, OnlyBar } from '../features/collections/CollectionMenu'
import { CollectionsProvider } from '../features/collections/store'
import { NotifyShell, OpenFromAddress } from '../features/notify/Shell'
import { SEARCH_PATH, useSearchKeys } from '../features/search/SearchPage'
import { SourceHints } from '../features/sources/Hints'
import { InvitationSign } from '../features/sources/Invitations'
import { OfflineKeeper } from '../features/offline/Keeper'
import { OfflineHint } from '../features/offline/OfflineHint'
import { useOffline } from '../features/offline/connection'
import { useCompact } from '../features/views/state'
import { UpdateBanner } from '../pwa/register'
import { viewCommand } from '../features/views/state'
import { useAuth } from '../state/auth'
import { Avatar } from './Avatar'
import { Dialog } from './Dialog'
import { Logo } from './Logo'
import { SessionDialog } from './SessionDialog'
import { WhatsNewBanner } from './WhatsNew'
import { NoticeHost } from './NoticeHost'
import { Sidebar, VIEWS } from './Sidebar'

/** Once per page load: the browser tells the server its time, so the log can warn of a clock far off (TIME-1 EC-14). */
let clockTold = false

export function AppShell() {
  const { t } = useTranslation()
  const { me } = useAuth()
  // Ctrl/⌘ K, Ctrl/⌘ Shift F and "/" open the search everywhere (TIME-22, as in nexnotes).
  useSearchKeys()
  const [open, setOpen] = useState(false)
  const location = useLocation()
  // The invitations stand in the head below 1024 px, beside "Schnell eintragen" on "Heute" above (TIME-28 AC-6).
  const compact = useCompact()

  useEffect(() => {
    if (clockTold) return
    clockTold = true
    void authApi.clock().catch(() => undefined)
  }, [])

  // A new page closes the profile on a phone.
  useEffect(() => setOpen(false), [location.pathname])

  const shown = me ? me.display_name || me.name : ''
  return (
    <PanelProvider>
      <CollectionsProvider>
      <div className="flex h-dvh overflow-hidden">
        <div className="hidden shrink-0 lg:flex" data-testid="sidebar-wide">
          <Sidebar resizable />
        </div>
        {open && (
          <div className="fixed inset-0 z-40 flex lg:hidden" role="presentation" data-testid="profile-sheet">
            <div className="relative shadow-2xl">
              <Sidebar onNavigate={() => setOpen(false)} />
              <button type="button" aria-label={t('common.close')} className="nt-btn nt-btn-ghost absolute top-3 right-3 w-10 px-0" onClick={() => setOpen(false)}>
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
            <button type="button" aria-label={t('common.close')} className="flex-1 bg-scrim" onClick={() => setOpen(false)} />
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b border-ink-700 bg-ink-900 px-3 py-2 lg:hidden" data-testid="compact-head">
            <NavLink to="/" aria-label={t('app.home')} className="inline-flex shrink-0">
              <Logo size={26} />
            </NavLink>
            <CollectionButton compact />
            <span className="ml-auto flex shrink-0 items-center gap-2.5">
              {compact && <InvitationSign />}
              <button type="button" onClick={() => setOpen(true)} aria-label={t('app.openMenu')} className="inline-flex items-center rounded-full" data-testid="profile-button">
                {me && <Avatar accountId={me.id} color={me.color} name={shown} stamp={me.avatar} size={30} />}
              </button>
            </span>
          </header>
          <UpdateBanner />
          <WhatsNewBanner />
          <OfflineHint />
          <TravelHint />
          <OnlyBar />
          {['/', '/day', '/week', '/month'].includes(location.pathname) && <SourceHints />}
          <main className="min-h-0 flex-1 overflow-y-auto" id="main">
            <Outlet />
          </main>
          <BottomBar />
        </div>
        <EventPanelHost />
        <NoticeHost />
        <LiveRefresh />
        <NotifyShell />
        <OpenFromAddress />
        <SessionDialog />
        <ResetNotice />
        <ViewKeys />
        <OfflineKeeper />
      </div>
      </CollectionsProvider>
    </PanelProvider>
  )
}

/** The bar at the bottom below 1024 px (TIME-28 AC-5): Heute, Woche, the round plus (a new event; off without a
 * connection, AC-3), Monat, Suchen; the view shown is marked. Its texts wrap rather than being cut with a large font
 * (EC-8), and it keeps clear of the round corners and the bar of a phone (`safe-area-inset-bottom`). */
function BottomBar() {
  const { t } = useTranslation()
  const location = useLocation()
  const { open } = usePanel()
  const offline = useOffline()
  const entry = (view: { to: string; key: string; end: boolean }) => (
    <NavLink
      key={view.to}
      to={view.to}
      end={view.end}
      className={({ isActive }) =>
        'flex min-h-14 min-w-0 flex-1 flex-col items-center justify-center gap-1 px-1 py-1.5 text-center text-xs leading-tight ' +
        (isActive || (view.to === '/week' && location.pathname === '/day') ? 'nt-bar-current font-semibold text-accent-400' : 'text-mist-400')
      }
      data-testid={view.to === SEARCH_PATH ? 'bottom-search' : `bottom-${view.to === '/' ? 'today' : view.to.slice(1)}`}
    >
      <span aria-hidden className="nt-bar-mark h-1 w-[22px] rounded-sm" />
      <span className="nt-wrap max-w-full">{t(view.key)}</span>
    </NavLink>
  )
  return (
    <nav aria-label={t('app.views')} className="flex shrink-0 items-center border-t border-ink-700 bg-ink-900 px-1 pb-[env(safe-area-inset-bottom)] lg:hidden" data-testid="bottom-bar">
      {entry(VIEWS[0])}
      {entry(VIEWS[1])}
      <span className="flex flex-1 justify-center">
        <button
          type="button"
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent-500 text-on-accent disabled:cursor-not-allowed disabled:bg-ink-700 disabled:text-mist-500"
          aria-label={offline ? t('offline.plusOff') : t('nav.newEvent')}
          disabled={offline}
          onClick={() => open({ kind: 'new' })}
          data-testid="bottom-plus"
        >
          <Plus className="h-6 w-6" strokeWidth={2.4} aria-hidden />
        </button>
      </span>
      {entry(VIEWS[2])}
      {entry({ to: SEARCH_PATH, key: 'search.field', end: false })}
    </nav>
  )
}

/** The keys of the views (TIME-12 AC-6, AC-13; TIME-11 AC-13): T day, W week, M month, H today, the arrows a step back or
 * on, K the jump to a calendar week, G the choice of a date, N a new event. Not while typing, nor while a dialog, a menu or the side panel is
 * open. */
function ViewKeys() {
  const navigate = useNavigate()
  const location = useLocation()
  const { target, open } = usePanel()
  const offline = useOffline()
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return
      const element = event.target as HTMLElement | null
      if (element?.closest?.('input, textarea, select, [contenteditable="true"]')) return
      if (target || document.querySelector('[role="dialog"], [role="menu"]')) return
      const pressed = event.key.toLowerCase()
      const views: Record<string, string> = { t: '/day', w: '/week', m: '/month', h: '/' }
      if (views[pressed]) {
        event.preventDefault()
        navigate(views[pressed])
      } else if (pressed === 'n') {
        event.preventDefault()
        // Without a connection nothing new (TIME-28 AC-3).
        if (!offline) open({ kind: 'new' })
      } else if (pressed === 'k') {
        if (viewCommand('jump')) event.preventDefault()
      } else if (pressed === 'g') {
        if (viewCommand('pick')) event.preventDefault()
      } else if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && ['/day', '/week', '/month'].includes(location.pathname)) {
        event.preventDefault()
        viewCommand(event.key === 'ArrowLeft' ? 'previous' : 'next')
      }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [navigate, location.pathname, target, open, offline])
  return null
}

/** "Times in New York (UTC−5)": only while the zone shown is not the home zone (TIME-3 AC-5, AC-11). */
export function TravelHint() {
  const { t } = useTranslation()
  const { display, device } = useDisplay()
  const hint = travelHint(display, device)
  if (!hint) return null
  return (
    <p role="status" className="border-b border-ink-700 bg-[var(--color-accent-wash)] px-4 py-2 text-sm text-mist-200" data-testid="travel-hint">
      {t('display.travel', { place: hint.place, offset: hint.offset })}
    </p>
  )
}

/** "Your password was reset through a link from the operator", until put away: whoever used the link, the account
 * learns of it, so an operator who set a password and signed in as somebody else does not stay unnoticed. */
function ResetNotice() {
  const { t } = useTranslation()
  const { me, setMe } = useAuth()
  const [busy, setBusy] = useState(false)
  if (!me?.password_reset_notice) return null
  const seen = async () => {
    setBusy(true)
    try {
      setMe(await authApi.resetNoticeSeen())
    } catch {
      // Shown again at the next load; nothing is lost.
      setMe({ ...me, password_reset_notice: false })
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog title={t('resetNotice.title')} onClose={() => void seen()}>
      <div className="space-y-3" data-testid="reset-notice">
        <p className="text-sm text-mist-200">{t('resetNotice.text')}</p>
        <p className="text-sm text-mist-400">{t('resetNotice.hint')}</p>
        <div className="flex justify-end">
          <button type="button" disabled={busy} className="nt-btn nt-btn-accent" onClick={() => void seen()}>
            {t('resetNotice.button')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
