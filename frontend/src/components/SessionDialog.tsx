/**
 * The session ended while the page was open. The page stays as it is underneath, so nothing written is lost:
 *
 * - **expired** (TIME-2 EC-4): sign in again right here, with the password or a provider; afterwards the page goes on
 *   and the editors save what waited (`SIGNED_IN_AGAIN_EVENT`).
 * - **blocked** (TIME-2 EC-5): "Your account is blocked". Nothing more is saved; the only way is out.
 *
 * Signing out while changes typed here still wait (offline, say) asks first (`SignOut.tsx`, the same question as the
 * button in the profile): they are lost with the session. "Warten" keeps the page, to save what is needed some other
 * way first.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi, type Methods } from '../api/client'
import { AuthField, Problem, troubleOf, waysIn, type Trouble } from '../pages/AuthPages'
import { useAuth } from '../state/auth'
import { Dialog } from './Dialog'
import { ProviderButtons } from './ProviderButtons'
import { SignOutWaiting, useSignOut } from './SignOut'

export function SessionDialog() {
  const { t } = useTranslation()
  const { interrupted, me, resume } = useAuth()
  const leaving = useSignOut()
  const [methods, setMethods] = useState<Methods | null>(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<'password' | 'code'>('password')
  const [problem, setProblem] = useState<Trouble>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (interrupted === 'expired') void waysIn().then(setMethods)
  }, [interrupted])

  if (!interrupted) return null

  if (interrupted === 'blocked') {
    return (
      <Dialog title={t('session.blockedTitle')} onClose={() => undefined}>
        <div className="space-y-4" data-testid="session-blocked">
          <p className="text-sm text-mist-300">{t('session.blockedText')}</p>
          {leaving.waiting > 0 ? (
            <SignOutWaiting count={leaving.waiting} onAnyway={leaving.anyway} onWait={leaving.wait} />
          ) : (
            <div className="flex justify-end">
              <button type="button" className="nt-btn nt-btn-ghost" data-testid="session-leave" onClick={leaving.ask}>
                {t('session.leave')}
              </button>
            </div>
          )}
        </div>
      </Dialog>
    )
  }

  const submit = async () => {
    setBusy(true)
    setProblem(null)
    try {
      const answer = step === 'code' ? await authApi.code(code.trim()) : await authApi.login(me?.email || me?.name || '', password)
      if ('second_factor' in answer) {
        setStep('code')
        return
      }
      setPassword('')
      setCode('')
      setStep('password')
      resume(await authApi.me())
    } catch (error) {
      setProblem(troubleOf(error))
    } finally {
      setBusy(false)
    }
  }

  const here = window.location.pathname + window.location.search
  return (
    <Dialog title={t('session.expiredTitle')} onClose={() => undefined}>
      <form
        className="space-y-4"
        data-testid="session-expired"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <p className="text-sm text-mist-300">{t('session.expiredText')}</p>
        <Problem problem={problem} />
        {step === 'code' ? (
          <AuthField label={t('auth.code.label')} value={code} onChange={setCode} autoComplete="one-time-code" autoFocus />
        ) : (
          (methods?.password ?? true) &&
          me?.sign_in !== 'oidc' && <AuthField label={t('auth.password')} value={password} onChange={setPassword} type="password" autoComplete="current-password" autoFocus />
        )}
        {(step === 'code' || me?.sign_in !== 'oidc') && (
          <button type="submit" disabled={busy} className="nt-btn nt-btn-accent w-full">
            {t('session.again')}
          </button>
        )}
      </form>
      {step === 'password' && <ProviderButtons providers={methods?.providers ?? []} next={here} withOr={me?.sign_in !== 'oidc'} />}
    </Dialog>
  )
}
