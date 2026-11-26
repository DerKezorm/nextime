/**
 * Settings › My account › Connections (plan "Einstellungen", Verbindungen; TIME-25, TIME-26, TIME-29), the cards in
 * their order: "Geräte" (the iPhone and iPad app), "Auf diesem Gerät installieren" (the web app), the other nex apps as
 * cards to fold (the shared module verbinden 0.1.2: one card per partner nextime has a use for, `in_use`; nextime's
 * choices open in the card, its form for a key typed in by hand under "Von Hand verbinden"), "API-Schlüssel" and
 * "KI-Werkzeuge (MCP)" of the shared key module. The card of AI
 * tools carries the parts of the shared module mcp: "Rechte je Werkzeug" per key or program, "Wartende Rückfragen".
 */
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { InstallCard } from '../../pwa/InstallCard'
import { useAuth } from '../../state/auth'
import { PendingRequests, ToolRights } from '../../vendor/nexmcp'
import { ConnectionsArea as AppConnections } from '../../vendor/nexverbinden'
import { ApiKeysCard, ToolsCard } from '../../vendor/nexschluessel'
import type { KeyRequest } from '../../vendor/nexschluessel/schluessel'
import { AppDevicesSection } from '../app/AppDevicesSection'
import { NexpaperChoices, NexpaperSection } from '../nexpaper/NexpaperSection'
import { NextasksChoices, NextasksSection } from '../nextasks/NextasksSection'
import { mcpRequest, useMcpWords } from '../mcp/McpPages'

// eslint-disable-next-line react-refresh/only-export-components
export const keyRequest: KeyRequest = <T,>(path: string, init: Parameters<KeyRequest>[1] = {}) => api<T>(path, init)

/** nextime's words for its rights and its areas, the calendars (TIME-26), as its dialog had them. */
// eslint-disable-next-line react-refresh/only-export-components
export function useKeyWords() {
  const { t } = useTranslation()
  return {
    rights: { read: t('keys.dialog.read'), write: t('keys.dialog.write') },
    rightHints: { read: t('keys.dialog.readHint'), write: t('keys.dialog.writeHint') },
    areas: t('keys.dialog.calendars'),
    areasAll: t('keys.dialog.calendarsAll'),
    areasSome: t('keys.dialog.calendarsSome'),
  }
}

export function ConnectionsArea() {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const { timeOptions } = useDisplay()
  const words = useKeyWords()
  const tools = useMcpWords()
  const when = (iso: string, language: string) => moment(iso, language, false, timeOptions)
  return (
    <div className="flex flex-col gap-6" data-testid="connections-area">
      <AppDevicesSection />
      <InstallCard />
      <AppConnections
        app="nextime"
        request={keyRequest}
        manual={{ nextasks: <NextasksSection bare />, nexpaper: <NexpaperSection bare /> }}
        // nextime's choices stand open in the card, for a connection by click and by hand alike (V23).
        extra={(view) => (view.partner === 'nextasks' ? <NextasksChoices /> : view.partner === 'nexpaper' ? <NexpaperChoices /> : null)}
        areaWord={t('keys.dialog.calendars')}
        language={i18n.language}
        when={when}
      />
      <ApiKeysCard request={keyRequest} things={t('settings.things')} thingsDative={t('settings.thingsDative')} words={words} docsHref="/api/v1/docs" operator={me?.role === 'operator'} when={when}>
        {/* What nextime's dialog said beside the choices (TIME-26 AC-1, EC-6): which calendars "all" means. */}
        <p className="m-0 text-xs leading-4 text-[var(--nexui-text-muted)]" data-testid="keys-below">
          {t('keys.below')}
        </p>
        <p className="m-0 text-xs leading-4 text-[var(--nexui-text-muted)]" data-testid="keys-hidden-note">
          {t('keys.dialog.hiddenNote')}
        </p>
      </ApiKeysCard>
      <ToolsCard
        request={keyRequest}
        words={words}
        when={when}
        extra={(key) => <ToolRights request={mcpRequest} keyId={key.id} words={tools} language={i18n.language} />}
      >
        {/* What an AI tool that writes may do (TIME-26 EC-6); since the module mcp beside the AI tools themselves. */}
        <p className="m-0 text-xs leading-4 text-[var(--nexui-text-muted)]" data-testid="keys-mcp-write">
          {t('keys.dialog.mcpWriteHint')}
        </p>
        <PendingRequests request={mcpRequest} words={tools} language={i18n.language} when={(iso) => when(iso, i18n.language)} viewHref={(entry) => `/mcp/requests/${entry.id}`} />
      </ToolsCard>
    </div>
  )
}
