/**
 * What each area of the settings shows. The cards themselves stay in their files (`AccountTab`, `ServerCards`,
 * `SignInCards`, `AccountsTab`, `LogCard`, `AboutArea`, `CalendarsArea`, `GoogleArea`, `DevicesTab`, `SharesArea`); an area only
 * picks them.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { serverApi, type ServerState } from '../../api/client'
import { DevicesTab } from '../../features/calendars/Devices'
import { ConnectionsArea } from '../../features/keys/ConnectionsArea'
import { RemindersArea } from '../../features/reminders/RemindersArea'
import { SharesArea } from '../../features/shares/SharesArea'
import { MoveArea } from '../../features/transfer/MoveArea'
import { AboutArea } from './AboutArea'
import { AccountsTab } from './AccountsTab'
import { AccountTab } from './AccountTab'
import type { AreaSlug } from './areas'
import { CalendarsArea } from './CalendarsArea'
import { GoogleArea } from './GoogleArea'
import { LogCard } from './LogCard'
import { BackupsCard, HttpsHint, LanguagesCard, LimitsCard, MailCard, ProxyHint, StateCard, SwitchesCard, useServerSettings } from './ServerCards'
import { SignInPart } from './SignInCards'

type ServerArea = 'general' | 'sign-in' | 'mail' | 'storage'

export function AreaContent({ area }: { area: AreaSlug }) {
  switch (area) {
    case 'profile':
    case 'security':
    case 'appearance':
      return <AccountTab part={area} />
    case 'calendars':
      return <CalendarsArea />
    case 'devices':
      return <DevicesTab />
    case 'shares':
      return <SharesArea />
    case 'reminders':
      return <RemindersArea />
    case 'move':
      return <MoveArea />
    case 'connections':
      return <ConnectionsArea />
    case 'google':
      return <GoogleArea />
    case 'accounts':
      return <AccountsTab />
    case 'log':
      return <LogCard />
    case 'about':
      return <AboutArea />
    default:
      return <ServerCardsOf area={area} />
  }
}

/**
 * The server's own settings, for the operator (TIME-1). The hint about an unnamed proxy concerns the sign-in brake and
 * the addresses: it stands in General and in Sign-in.
 */
function ServerCardsOf({ area }: { area: ServerArea }) {
  const server = useServerSettings()
  const state = useServerState(area === 'general')
  if (!server.settings) return null
  return (
    <div className="space-y-6" data-testid={`server-${area}`}>
      {(area === 'general' || area === 'sign-in') && <ProxyHint server={server} />}
      {area === 'general' && (
        <>
          {state && !state.https && <HttpsHint />}
          <LimitsCard server={server} />
          <SwitchesCard server={server} />
          <LanguagesCard />
          <VersionAndState state={state} />
        </>
      )}
      {area === 'sign-in' && <SignInPart server={server} />}
      {area === 'mail' && <MailCard server={server} />}
      {area === 'storage' && <BackupsCard server={server} />}
    </div>
  )
}

/** The state of the server (TIME-1 AC-6, AC-7, AC-9): HTTPS, the internet, the version and a newer one. */
function useServerState(wanted: boolean): ServerState | null {
  const [state, setState] = useState<ServerState | null>(null)
  useEffect(() => {
    if (wanted) serverApi.state().then(setState, () => undefined)
  }, [wanted])
  return state
}

/** After the cards General shares with nexnotes, nextime's own: the running version with "New version" beside it
 * (TIME-1 AC-7) and the box "State" with HTTPS and the internet (AC-9). The hint without HTTPS (AC-6) stands on top,
 * beside the hint about the proxy. */
function VersionAndState({ state }: { state: ServerState | null }) {
  const { t } = useTranslation()
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-mist-400" data-testid="server-version">
          {state ? t('server.version', { version: state.version }) : ''}
        </span>
        {state?.newer && (
          <span className="nt-chosen rounded-full px-2.5 py-1 text-xs font-medium" data-testid="server-newer">
            {t('server.newer', { version: state.newer })}
          </span>
        )}
      </div>
      <StateCard state={state} />
    </>
  )
}
