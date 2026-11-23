/**
 * Settings (TIME-2 AC-7), laid out as in nexnotes so that whoever comes from another nex app finds the way at once:
 * "My account" (Profile, Security, Appearance, Calendars and accounts, Shares, On iPhone and co, Connections,
 * Reminders) for every account, "Server" (General, Sign-in, Mail, Storage and backups, Set up Google, Accounts, Log)
 * for the operator, "About nextime" for everyone at the end. Each area has its own address (`/settings/<slug>`,
 * `areas.ts`), `/settings` is the profile. An area someone may not open, or an address of before, leads on with the
 * address replaced, so the navigation always marks where one is.
 */
import { useTranslation } from 'react-i18next'
import { Navigate, useLocation, useParams } from 'react-router-dom'

import { useTitle } from '../lib/title'
import { useOffline } from '../features/offline/connection'
import { useAuth } from '../state/auth'
import { findArea, formerPath, resolveArea } from './settings/areas'
import { SignOutCard } from './settings/AccountTab'
import { AreaContent } from './settings/SettingsAreas'
import { SettingsJump, SettingsNav } from './settings/SettingsNav'

export function SettingsPage() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const { tab: asked } = useParams()
  const { search } = useLocation()
  const where = resolveArea(asked, me?.role, search)
  const area = 'area' in where ? findArea(where.area) : undefined
  const title = area ? t(`settings.areas.${area.key}.title`) : ''
  // Without a connection the settings are to read only (TIME-28 AC-3).
  const offline = useOffline()
  useTitle(area ? `${title} · ${t('settings.title')}` : t('settings.title'))
  if (!me) return null
  if ('redirect' in where) return <Navigate to={where.redirect} replace />
  if (!area) return null
  return (
    <div className="@container mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
      <div className="flex flex-col gap-5 @min-[700px]:flex-row @min-[700px]:items-start @min-[700px]:gap-8">
        <SettingsNav me={me} current={area.slug} />
        <SettingsJump me={me} current={area.slug} />
        <div className="min-w-0 flex-1 space-y-5" data-testid={`settings-area-${area.slug}`}>
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-mist-100">{title}</h1>
            <p className="mt-1 text-sm text-mist-400">{t(`settings.areas.${area.key}.text`)}</p>
          </div>
          {offline && (
            <p role="status" className="rounded-[12px] border border-warn-500/40 bg-warn-wash px-3.5 py-2.5 text-sm text-warn-500" data-testid="settings-offline">
              {t('offline.settingsOff')}
            </p>
          )}
          <fieldset disabled={offline} className="min-w-0 space-y-6" data-testid="settings-fieldset">
            <AreaContent key={area.slug} area={area.slug} />
          </fieldset>
          {offline && area.slug === 'profile' && <SignOutCard viaProvider={me.sign_in === 'oidc'} />}
        </div>
      </div>
    </div>
  )
}

/** The addresses of before, `/account/<tab>` and `/server/<page>`: they lead to their area, with the query kept. */
export function FormerSettingsAddress() {
  const { pathname, search } = useLocation()
  return <Navigate to={formerPath(pathname, search)} replace />
}
