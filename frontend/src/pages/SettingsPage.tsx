/**
 * Settings (TIME-2 AC-7, plan "Einstellungen") in the frame of the ui kit: `SettingsLayout` with `SettingsNav` (the
 * account at the top, "Mein Konto", "Server" for the operator, "Über nextime" set apart at the end) and one
 * `SettingsPage` per area: its title, one line, then only cards. Each area has its own address (`/settings/<slug>`,
 * `areas.ts`). `/settings` alone is the list on a phone and the profile beside the navigation on a wider page. An area
 * someone may not open, or an address of before, leads on with the address replaced, so the navigation always marks
 * where one is. Without a connection the settings are to read only; signing out stays (TIME-28 AC-3, EC-1).
 */
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useLocation, useParams } from 'react-router-dom'

import { Avatar } from '../components/Avatar'
import { useOffline } from '../features/offline/connection'
import { useTitle } from '../lib/title'
import { useAuth } from '../state/auth'
import {
  areaText,
  languageOf,
  NEXUI_TEXTS,
  NexuiLanguage,
  Notice,
  SettingsLayout,
  SettingsNav,
  SettingsPage as Area,
  type SettingsGroup,
} from '../vendor/nexui'
import { ABOUT_AREA, areaPath, areasFor, findArea, formerPath, GROUPS, resolveArea, type Area as AreaInfo } from './settings/areas'
import { SignOut } from './settings/AccountTab'
import { AreaContent } from './settings/SettingsAreas'

const APP = 'nextime'

export function SettingsPage() {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const { tab: asked } = useParams()
  const { search } = useLocation()
  const where = resolveArea(asked, me?.role, search)
  const area = 'area' in where ? findArea(where.area) : undefined
  const things = t('settings.things')
  const words = (info: AreaInfo) =>
    info.own
      ? {
          title: t(`settings.areas.${info.key}.title`),
          text: t(`settings.areas.${info.key}.text`),
        }
      : (areaText(NEXUI_TEXTS[languageOf(i18n.language)], info.slug, {
          app: APP,
          things,
        }) ?? { title: info.slug, text: '' })
  const title = area ? words(area).title : ''
  // Without a connection the settings are to read only (TIME-28 AC-3).
  const offline = useOffline()
  useTitle(area ? `${title} · ${t('settings.title')}` : t('settings.title'))
  if (!me) return null
  if ('redirect' in where) return <Navigate to={where.redirect} replace />
  if (!area) return null
  const areas = areasFor(me.role)
  const groups: SettingsGroup[] = GROUPS.map((group) => ({
    id: group,
    areas: areas
      .filter((entry) => entry.group === group)
      .map((entry) => ({
        slug: entry.slug,
        icon: entry.icon,
        href: areaPath(entry.slug),
        title: entry.own ? words(entry).title : undefined,
      })),
  }))
  const shown = me.display_name || me.name
  const own = area.own ? words(area) : null
  return (
    <NexuiLanguage language={i18n.language}>
      <SettingsLayout
        list={asked === undefined}
        back="/settings"
        link={Link}
        nav={
          <SettingsNav
            app={APP}
            things={things}
            groups={groups}
            about={{ href: areaPath(ABOUT_AREA.slug), slug: ABOUT_AREA.slug }}
            current={area.slug}
            account={{
              name: shown,
              role: t(`roles.${me.role}`),
              picture: <Avatar accountId={me.id} color={me.color} name={shown} stamp={me.avatar} size={40} />,
            }}
            link={Link}
          />
        }
      >
        <Area slug={area.slug} app={APP} things={things} title={own?.title} subtitle={own?.text}>
          {offline && (
            <div role="status" data-testid="settings-offline">
              <Notice tone="warn">{t('offline.settingsOff')}</Notice>
            </div>
          )}
          <fieldset disabled={offline} className="m-0 flex min-w-0 flex-col gap-6 border-0 p-0" data-testid="settings-fieldset">
            <AreaContent key={area.slug} area={area.slug} />
          </fieldset>
          {offline && area.slug === 'profile' && <SignOut />}
        </Area>
      </SettingsLayout>
    </NexuiLanguage>
  )
}

/** The addresses of before, `/account/<tab>` and `/server/<page>`: they lead to their area, with the query kept. */
export function FormerSettingsAddress() {
  const { pathname, search } = useLocation()
  return <Navigate to={formerPath(pathname, search)} replace />
}
