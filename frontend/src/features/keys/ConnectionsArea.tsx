/**
 * Settings › My account › Connections (plan "Einstellungen", Verbindungen; TIME-25, TIME-26, TIME-29), the cards in
 * their order: "Geräte" (the iPhone and iPad app), "Auf diesem Gerät installieren" (the web app), the other nex apps as
 * cards to fold (nextasks, nexpaper), "API-Schlüssel" and "KI-Werkzeuge (MCP)" of the shared key module.
 */
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { InstallCard } from '../../pwa/InstallCard'
import { useAuth } from '../../state/auth'
import { ApiKeysCard, ToolsCard } from '../../vendor/nexschluessel'
import type { KeyRequest } from '../../vendor/nexschluessel/schluessel'
import { AppDevicesSection } from '../app/AppDevicesSection'
import { NexpaperSection } from '../nexpaper/NexpaperSection'
import { NextasksSection } from '../nextasks/NextasksSection'

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
  const { t } = useTranslation()
  const { me } = useAuth()
  const { timeOptions } = useDisplay()
  const words = useKeyWords()
  const when = (iso: string, language: string) => moment(iso, language, false, timeOptions)
  return (
    <div className="flex flex-col gap-6" data-testid="connections-area">
      <AppDevicesSection />
      <InstallCard />
      <NextasksSection />
      <NexpaperSection />
      <ApiKeysCard request={keyRequest} things={t('settings.things')} words={words} docsHref="/api/v1/docs" operator={me?.role === 'operator'} when={when}>
        {/* What nextime's dialog said beside the choices (TIME-26 AC-1, EC-6): which calendars "all" means, what an AI
            tool that writes may do. */}
        <p className="m-0 text-xs leading-4 text-[var(--nexui-text-muted)]" data-testid="keys-below">
          {t('keys.below')}
        </p>
        <p className="m-0 text-xs leading-4 text-[var(--nexui-text-muted)]" data-testid="keys-hidden-note">
          {t('keys.dialog.hiddenNote')}
        </p>
        <p className="m-0 text-xs leading-4 text-[var(--nexui-text-muted)]" data-testid="keys-mcp-write">
          {t('keys.dialog.mcpWriteHint')}
        </p>
      </ApiKeysCard>
      <ToolsCard request={keyRequest} words={words} when={when} />
    </div>
  )
}
