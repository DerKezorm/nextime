/**
 * The app connects by address (TIME-29 AC-5): it opens `/app-connect?state=<random>` (with `challenge` from the module
 * on) in the system's sign-in sheet. Without a session the page goes to the sign-in first (password, provider with
 * passkey, second factor) and comes back here; with one the module's page asks "nextime-App mit deinem Konto
 * verbinden?" (shared module geraete, `AppConnectPage`). "Verbinden" hands a one-time code to the app
 * (`nextime://connected?code=…&state=…`), "Abbrechen" says so to it (`…?error=cancelled&state=…`). Nothing happens
 * without the click.
 */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useLocation } from 'react-router-dom'

import { Logo } from '../../components/Logo'
import { ThemeSwitcher } from '../../components/ThemeSwitcher'
import { go } from '../../lib/providers'
import { useAuth } from '../../state/auth'
import { AppConnectPage as ModulePage } from '../../vendor/nexgeraete'
import { GERAETE_BASE, deviceRequest } from './devices'

function frame(card: ReactNode): ReactNode {
  return (
    <div className="nt-scroll flex min-h-dvh flex-col overflow-y-auto">
      <header className="flex items-center justify-between px-5 py-4">
        <Logo withWordmark />
        <ThemeSwitcher />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[6vh] pb-10">{card}</main>
    </div>
  )
}

export function AppConnectPage() {
  const { i18n } = useTranslation()
  const { status, me } = useAuth()
  const location = useLocation()

  if (status === 'loading') return null
  if (status === 'setup') return <Navigate to="/setup" replace />
  if (status === 'signedOut' || status === 'offline') return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  return (
    <ModulePage
      request={deviceRequest}
      base={GERAETE_BASE}
      app="nextime"
      scheme="nextime"
      account={me ? { name: me.display_name || me.name, login: me.name } : null}
      language={i18n.language}
      search={location.search}
      go={go}
      frame={frame}
    />
  )
}
