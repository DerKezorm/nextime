/**
 * Signing out of this browser, the one way for every place that offers it (My account, TIME-2 AC-9,
 * and the dialog of a blocked account, EC-4). Changes typed here that still wait for the server (offline, say) are
 * lost with the session: then it asks first, "Sign out anyway" or "Wait". Only the session of nextime ends; whoever
 * came in through a provider stays signed in there.
 */
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { waitingChanges } from '../state/account'
import { useAuth } from '../state/auth'

/** `ask` signs out at once, or first sets `waiting` to the changes that would be lost; `wait` takes the question back. */
// eslint-disable-next-line react-refresh/only-export-components
export function useSignOut() {
  const { signOut } = useAuth()
  const [waiting, setWaiting] = useState(0)
  const ask = useCallback(() => {
    const now = waitingChanges()
    if (now > 0) setWaiting(now)
    else void signOut()
  }, [signOut])
  const anyway = useCallback(() => void signOut(), [signOut])
  const wait = useCallback(() => setWaiting(0), [])
  return { ask, waiting, anyway, wait }
}

/** The question while changes wait: how many are lost, "Sign out anyway" and "Wait". */
export function SignOutWaiting({ count, onAnyway, onWait }: { count: number; onAnyway: () => void; onWait: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="space-y-3 rounded-[10px] border border-warn-500/40 px-3.5 py-3" data-testid="sign-out-waiting">
      <p className="text-sm font-medium text-warn-500">{t('session.waitingText', { count })}</p>
      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className="nt-btn nt-btn-ghost" data-testid="sign-out-anyway" onClick={onAnyway}>
          {t('session.signOutAnyway')}
        </button>
        <button type="button" className="nt-btn nt-btn-accent" data-testid="sign-out-wait" onClick={onWait}>
          {t('session.wait')}
        </button>
      </div>
    </div>
  )
}
