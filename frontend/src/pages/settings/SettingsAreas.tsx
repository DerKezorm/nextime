/**
 * What each area of the settings shows (plan "Einstellungen"): only cards, in the plan's order. The shared areas take
 * the cards of the modules (konten, oidc, post, sicherung, log, schluessel) and of the ui kit; nextime's own areas
 * (Calendars and accounts, Shares, On iPhone and co, Take along and move, Set up Google) keep their cards.
 */
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { DevicesTab } from '../../features/calendars/Devices'
import { ConnectionsArea, keyRequest } from '../../features/keys/ConnectionsArea'
import { mcpRequest, useMcpWords } from '../../features/mcp/McpPages'
import { RemindersArea } from '../../features/reminders/RemindersArea'
import { SharesArea } from '../../features/shares/SharesArea'
import { MoveArea } from '../../features/transfer/MoveArea'
import { MailCard } from '../../vendor/nexpost/MailCard'
import type { PostRequest } from '../../vendor/nexpost/mailserver'
import { verbindenRequest } from '../../features/connect/ConnectPages'
import { McpOperatorSettings } from '../../vendor/nexmcp'
import { ConnectionsOperatorCard } from '../../vendor/nexverbinden'
import { InterfacesCard } from '../../vendor/nexschluessel'
import { AboutArea } from './AboutArea'
import { AccountsTab } from './AccountsTab'
import { AccountTab } from './AccountTab'
import { AppearanceArea } from './AppearanceArea'
import type { AreaSlug } from './areas'
import { CalendarsArea } from './CalendarsArea'
import { FeaturesCard, LimitsCard } from './GeneralCards'
import { LanguagesArea } from './LanguagesArea'
import { GoogleArea } from './GoogleArea'
import { LogCard } from './LogCard'
import { useServerSettings } from './ServerCards'
import { SettingsLoadProblem } from './SettingsLoadProblem'
import { SignInArea } from './SignInArea'
import { StorageArea } from './StorageArea'

const mailRequest: PostRequest = <T,>(path: string, init: Parameters<PostRequest>[1] = {}) => api<T>(path, init)

export function AreaContent({ area }: { area: AreaSlug }) {
  switch (area) {
    case 'profile':
    case 'security':
      return <AccountTab part={area} />
    case 'appearance':
      return <AppearanceArea />
    case 'calendars':
      return <CalendarsArea />
    case 'devices':
      // "Auf iPhone und Co" is the calendar account alone now (TIME-20); the iPhone and iPad app is under Connections.
      return <DevicesTab />
    case 'shares':
      return <SharesArea />
    case 'notifications':
      return <RemindersArea />
    case 'move':
      return <MoveArea />
    case 'connections':
      return <ConnectionsArea />
    case 'general':
      return <GeneralArea />
    case 'server-connections':
      return <ServerConnectionsArea />
    case 'sign-in':
      return <SignInArea />
    case 'mail':
      return <MailArea />
    case 'storage':
      return <StorageArea />
    case 'google':
      return <GoogleArea />
    case 'accounts':
      return <AccountsTab />
    case 'log':
      return <LogCard />
    case 'about':
      return <AboutArea />
  }
}

/** Server › Mail (post 1.1.0): the module's card with nextime's sentence; moments in the account's clock and zone. */
function MailArea() {
  const { t } = useTranslation()
  const { timeOptions } = useDisplay()
  return <MailCard request={mailRequest} subtitle={t('server.mailSubtitle')} when={(iso, language) => moment(iso, language, false, timeOptions)} />
}

/** Server › General (TIME-1): Limits, Features for every account, Languages. "Andere nex-Apps" and "Schnittstellen"
 * stand under Server › Connections (E67). */
function GeneralArea() {
  const server = useServerSettings()
  if (!server.settings) return <SettingsLoadProblem server={server} />
  return (
    <div className="flex flex-col gap-6" data-testid="server-general">
      <LimitsCard server={server} />
      <FeaturesCard server={server} />
      <LanguagesArea />
    </div>
  )
}

/** Server › Connections (E67, ui 1.2.1): the operator's mirror of My account › Connections. First "Andere nex-Apps"
 * (the shared module verbinden), then the key module's "Schnittstellen" with the operator's settings of the shared
 * module mcp, the same cards with the same props as under General before. */
function ServerConnectionsArea() {
  const { i18n } = useTranslation()
  const tools = useMcpWords()
  return (
    <div className="flex flex-col gap-6" data-testid="server-connections">
      {/* "Andere nex-Apps": connections with other nex apps on or off, their addresses (the shared module verbinden). */}
      <ConnectionsOperatorCard app="nextime" request={verbindenRequest} language={i18n.language} />
      {/* The shared module mcp under "MCP-Zugang": programs may connect, the highest level, blocked tools. */}
      <InterfacesCard
        request={keyRequest}
        docsHref="/api/v1/docs"
        after={{ mcp: <McpOperatorSettings request={mcpRequest} words={tools} language={i18n.language} /> }}
      />
    </div>
  )
}
