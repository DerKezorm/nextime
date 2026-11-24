/**
 * The app connects by address (TIME-29 AC-5), as nexnotes: it opens `/app-connect?state=<random>` in the system's
 * sign-in sheet. Without a session the page goes to the sign-in first (password, provider with passkey, second factor)
 * and comes back here; with one it asks at once "Die nextime-App mit diesem Konto verbinden?". "Verbinden" hands a
 * one-time code to the app (`nextime://connected?code=…&state=…`), "Abbrechen" says so to it
 * (`…?error=cancelled&state=…`). Nothing happens without the click.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useLocation, useSearchParams } from 'react-router-dom'

import { problemText } from '../../lib/errors'
import { go } from '../../lib/providers'
import { AuthFrame } from '../../pages/AuthPages'
import { useAuth } from '../../state/auth'
import { appDevicesApi, cancelLink, validState } from './api'

export function AppConnectPage() {
  const { t } = useTranslation()
  const { status, me } = useAuth()
  const location = useLocation()
  const [params] = useSearchParams()
  const state = params.get('state')
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  if (status === 'loading') return null
  if (status === 'setup') return <Navigate to="/setup" replace />
  if (status === 'signedOut' || status === 'offline') return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  if (!validState(state)) {
    return (
      <AuthFrame title={t('appConnect.title')}>
        <p role="alert" className="text-sm text-bad-500" data-testid="app-connect-invalid">
          {t('appConnect.invalid')}
        </p>
      </AuthFrame>
    )
  }

  const connect = async () => {
    setBusy(true)
    setProblem('')
    try {
      const { link } = await appDevicesApi.connect(state)
      setDone(true)
      go(link)
    } catch (error) {
      setProblem(problemText(error))
      setBusy(false)
    }
  }

  if (done) {
    return (
      <AuthFrame title={t('appConnect.doneTitle')}>
        <p className="text-sm text-mist-300" role="status" data-testid="app-connect-done">
          {t('appConnect.done')}
        </p>
      </AuthFrame>
    )
  }
  return (
    <AuthFrame title={t('appConnect.title')} text={t('appConnect.text', { account: me?.display_name || me?.name || '' })}>
      {problem && (
        <p role="alert" className="mb-3 text-sm text-bad-500" data-testid="app-connect-problem">
          {problem}
        </p>
      )}
      <div className="flex flex-col gap-2">
        <button type="button" disabled={busy} onClick={() => void connect()} className="nt-btn nt-btn-accent w-full justify-center" data-testid="app-connect-confirm">
          {t('appConnect.confirm')}
        </button>
        <button type="button" disabled={busy} onClick={() => go(cancelLink(state))} className="nt-btn nt-btn-ghost w-full justify-center" data-testid="app-connect-cancel">
          {t('common.cancel')}
        </button>
      </div>
    </AuthFrame>
  )
}
