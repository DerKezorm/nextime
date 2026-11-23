/**
 * The hints of TIME-28 over the page: without a connection in orange "Ohne Verbindung · Stand von heute 15:52 · nur
 * lesen" and below it what still works and what does not (AC-3, drawing "Handy-Offline"); in a browser tab without
 * anything kept "Ohne Verbindung · nur lesen". A device that cannot keep anything says so once (EC-2). Inside the views
 * "Dieser Zeitraum erscheint erst mit Verbindung." where a window reaches past what is kept (EC-3).
 */
import { CloudOff, X } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useDisplay } from '../../lib/useDisplay'
import { usePwa } from '../../pwa/worker'
import { useNow } from '../views/state'
import { useOffline } from './connection'
import { stateTime } from './kept'
import { useKept } from './snapshot'

export function OfflineHint() {
  const { t } = useTranslation()
  const offline = useOffline()
  const { snapshot, failed } = useKept()
  const { standalone } = usePwa()
  const { zone, twelve, language } = useDisplay()
  const [fullSeen, setFullSeen] = useState(false)
  const now = useNow()
  if (offline) {
    const state = snapshot
      ? t('offline.state', { when: stateTime(snapshot.takenAt, now, zone, language, twelve, { today: t('offline.today'), yesterday: t('offline.yesterday') }) })
      : t('offline.stateNone')
    return (
      <div role="status" className="flex shrink-0 gap-2.5 border-b border-warn-500/40 bg-warn-wash px-4 py-2 text-sm" data-testid="offline-hint">
        <CloudOff className="mt-0.5 h-4 w-4 shrink-0 text-warn-500" aria-hidden />
        <span className="min-w-0 space-y-0.5">
          <strong className="nt-wrap block font-semibold text-warn-500" data-testid="offline-state">
            {state}
          </strong>
          <span className="nt-wrap block text-[13px] leading-snug text-mist-300" data-testid="offline-text">
            {snapshot ? t('offline.text') : t('offline.textNone')}
          </span>
        </span>
      </div>
    )
  }
  if (failed && standalone && !fullSeen) {
    return (
      <div role="status" className="flex shrink-0 items-start gap-2.5 border-b border-ink-700 bg-[var(--color-accent-wash)] px-4 py-2 text-sm text-mist-200" data-testid="offline-full">
        <span className="nt-wrap min-w-0 flex-1">{t('offline.full')}</span>
        <button type="button" className="shrink-0 rounded-full p-1 text-mist-500 hover:text-mist-100" aria-label={t('offline.fullAway')} onClick={() => setFullSeen(true)}>
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
    )
  }
  return null
}

/** "Dieser Zeitraum erscheint erst mit Verbindung." (EC-3), in a view whose window reaches past what is kept. */
export function OutsideKept() {
  const { t } = useTranslation()
  return (
    <p role="status" className="nt-wrap rounded-[12px] border border-warn-500/40 bg-warn-wash px-3.5 py-2.5 text-sm text-warn-500" data-testid="offline-outside">
      {t('offline.outside')}
    </p>
  )
}
