/**
 * Signing out of this browser, the one way for every place that offers it (My account, TIME-2 AC-9,
 * and the dialog of a blocked account, EC-4). Changes typed here that still wait for the server (offline, say) are
 * lost with the session: then it asks first, "Sign out anyway" or "Wait". Only the session of nextime ends; whoever
 * came in through a provider stays signed in there.
 */
import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { waitingChanges } from '../state/account'
import { Notice } from '../vendor/nexui'
import { AccountButton } from './AccountButton'
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
    <div role="alert" data-testid="sign-out-waiting">
      <Notice tone="warn" title={t('session.waitingText', { count })}>
        <div className="mt-2 flex flex-wrap gap-2">
          <AccountButton name="back" testId="sign-out-wait" onClick={onWait}>
            {t('session.wait')}
          </AccountButton>
          <AccountButton name="signOut" testId="sign-out-anyway" onClick={onAnyway}>
            {t('session.signOutAnyway')}
          </AccountButton>
        </div>
      </Notice>
    </div>
  )
}
