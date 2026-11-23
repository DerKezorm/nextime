import { Fragment, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { AppShell } from './components/AppShell'
import { NotFoundPage } from './components/NotFound'
import { FEATURE_ROUTES } from './extensions'
import { SetupLinkPage } from './features/calendars/Devices'
import { CalendarSettingsPage } from './features/notify/CalendarSettings'
import { SearchPage } from './features/search/SearchPage'
import { GuestPage } from './features/shares/GuestPage'
import { TrashPage } from './features/trash/TrashPage'
import { InvitePage, LoginPage, ResetPage, SetupPage } from './pages/AuthPages'
import { FormerSettingsAddress, SettingsPage } from './pages/SettingsPage'
import { GridView } from './features/views/GridView'
import { MonthView } from './features/views/MonthView'
import { TodayView } from './features/views/TodayView'
import { useAuth } from './state/auth'

/**
 * Everything behind the sign-in: without an account the page goes to the sign-in, and comes back after; while nextime
 * has no account yet, every page leads to the setup (TIME-1 EC-4). An account that still has to set up its second
 * factor (the operator requires one) reaches the settings only, at Security (an address of before leads there too, so
 * the answer of a provider after linking keeps its message).
 *
 * The calendar features add their pages as routes inside the shell (`FEATURE_ROUTES` in `extensions.ts`). Another
 * account in the same tab gets the shell drawn anew; the stores outside the tree forget the account before through
 * `state/account.ts`.
 */
function SignedIn({ children }: { children: ReactNode }) {
  const { status, me, ended } = useAuth()
  const location = useLocation()
  if (status === 'loading') return null
  if (status === 'setup') return <Navigate to="/setup" replace />
  if (status === 'signedOut' || status === 'offline') return <Navigate to={`/login?${ended ? 'ended=1&' : ''}next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  if (me?.second_factor_setup_required && !/^\/(settings|account)(\/|$)/.test(location.pathname)) return <Navigate to="/settings/security" replace />
  // Drawn anew for every account: nothing of the account before stays in the tab.
  return <Fragment key={me?.id ?? 'nobody'}>{children}</Fragment>
}

export default function App() {
  return (
    <Routes>
      <Route path="setup" element={<SetupPage />} />
      <Route path="login" element={<LoginPage />} />
      <Route path="invite/:token" element={<InvitePage />} />
      <Route path="reset/:token" element={<ResetPage />} />
      <Route path="setup/:code" element={<SetupLinkPage />} />
      {/* A guest's read link (TIME-19 AC-13): no account, nothing else of nextime. */}
      <Route path="abo/:token" element={<GuestPage />} />
      <Route
        element={
          <SignedIn>
            <AppShell />
          </SignedIn>
        }
      >
        <Route index element={<TodayView />} />
        <Route path="day" element={<GridView kind="day" />} />
        <Route path="week" element={<GridView kind="week" />} />
        <Route path="month" element={<MonthView />} />
        {/* The search (TIME-22) and the trash (TIME-23). */}
        <Route path="search" element={<SearchPage />} />
        <Route path="trash" element={<TrashPage />} />
        <Route path="settings" element={<SettingsPage />} />
        {/* A calendar's settings and notifications (TIME-21). */}
        <Route path="calendar/:id" element={<CalendarSettingsPage />} />
        <Route path="settings/:tab" element={<SettingsPage />} />
        {/* The addresses of before ("My account" with its tabs, the pages of "Server") lead to their area. */}
        <Route path="account" element={<FormerSettingsAddress />} />
        <Route path="account/:tab" element={<FormerSettingsAddress />} />
        <Route path="server" element={<FormerSettingsAddress />} />
        <Route path="server/:page" element={<FormerSettingsAddress />} />
        {FEATURE_ROUTES.map((route) => (
          <Route key={route.path} path={route.path} element={route.element} />
        ))}
        {/* An address no page answers to says so, instead of silently showing the start. */}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
