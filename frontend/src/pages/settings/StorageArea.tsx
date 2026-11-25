/**
 * Server › Storage and backups (TIME-1 AC-2, AC-8): the cards "Speicher" and "Sicherung" of the shared module
 * sicherung (`vendor/nexsicherung`, bauplaene/sicherung 1.0.1) over its routes (`/api/server/storage`). nextime hands
 * it its sentence of what a backup holds, the names of its parts and what a restore changes: attachments, events and
 * accounts, by number only (the operator never sees calendars or events of others, TIME-2 AC-3).
 */
import { useTranslation } from 'react-i18next'

import { tabId } from '../../api/client'
import { useAuth } from '../../state/auth'
import { StorageArea as SharedStorageArea } from '../../vendor/nexsicherung/StorageArea'
import { makeSicherungApi, type Brief } from '../../vendor/nexsicherung/sicherung'
import { languageOf } from '../../vendor/nexui'

/** nextime's own client for the module: every request names its tab (changes are refused without it). */
const api = makeSicherungApi((path, init) => {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  headers.set('X-Nextime-Client', tabId())
  return fetch(path, { ...init, headers })
}, '/api/server/storage')

const number = (value: unknown) => (typeof value === 'number' ? value : 0)

export function StorageArea() {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const details = (brief: Brief) => {
    const found = brief.details ?? {}
    return (
      <div className="flex flex-col gap-1" data-testid="restore-events">
        <p className="m-0">
          {t('server.restoreChanges', {
            add: t('server.countFiles', { count: number(found.would_add) }),
            change: t('server.countFiles', { count: number(found.would_change) }),
            remove: t('server.countFiles', { count: number(found.would_remove) }),
          })}
        </p>
        <p className="m-0">{t('server.restoreEvents', { add: number(found.events_add), change: number(found.events_change), remove: number(found.events_remove) })}</p>
        <p className="m-0 text-xs">{t('server.restoreAccounts', { add: number(found.accounts_add), remove: number(found.accounts_remove) })}</p>
      </div>
    )
  }
  const partLabel = (key: string) => (i18n.exists(`server.parts.${key}`) ? t(`server.parts.${key}`) : undefined)
  return (
    <SharedStorageArea
      app="nextime"
      things={t('settings.things')}
      holds={t('server.backupHolds')}
      api={api}
      password={me?.sign_in === 'password'}
      partLabel={partLabel}
      details={details}
      language={languageOf(i18n.language)}
    />
  )
}
