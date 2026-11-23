/**
 * The installable web app (TIME-28), as nexnotes docks it (NOTES-23): the service worker (`public/sw.js`) is signed up
 * at the start of a built page, a new version waiting shows as a bar over the page ("Es gibt eine neue Version von
 * nextime." with "Neu laden", EC-7), and signing out or another account makes the worker forget what is not the
 * shell. What is kept for reading without a connection lies in IndexedDB (`features/offline/snapshot.ts`), not in the
 * worker.
 */
import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { onAccountChange } from '../state/account'
import { startWorker, takeUpdate, tell, usePwa } from './worker'

export function UpdateBanner() {
  const { t } = useTranslation()
  const { update } = usePwa()
  if (!update) return null
  return (
    <p role="status" className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-accent-500/30 bg-[var(--color-accent-wash)] px-4 py-2 text-sm text-mist-100" data-testid="pwa-update">
      <RefreshCw className="h-4 w-4 shrink-0 text-accent-400" aria-hidden />
      <span>{t('pwa.update')}</span>
      <button type="button" className="min-h-11 font-semibold text-accent-400 hover:underline" onClick={takeUpdate}>
        {t('pwa.reload')}
      </button>
    </p>
  )
}

// Signing out and another account: anything outside the shell goes from the worker too.
onAccountChange(() => void tell({ type: 'nextime-forget' }))

if (import.meta.env.PROD) startWorker()
