/**
 * The session ended while the page was open. The page stays as it is underneath, so nothing written is lost:
 *
 * - **expired** (TIME-2 EC-4): sign in again right here; afterwards the page goes on and the editors save what waited
 *   (`SIGNED_IN_AGAIN_EVENT`). The dialog shows the parts of the shared account module for each step the server names
 *   (`sessionExpiredParts(next)`, konten 0.3.5): the password and the providers, then the code or the passkey.
 * - **blocked** (TIME-2 EC-5): "Your account is blocked". Nothing more is saved; the only way is out.
 *
 * Signing out while changes typed here still wait (offline, say) asks first (`SignOut.tsx`, the same question as the
 * button in the profile): they are lost with the session. "Warten" keeps the page, to save what is needed some other
 * way first.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi, type Methods } from '../api/client'
import { passkeysSupported, signInWithPasskey } from '../lib/passkeys'
import { AuthField, Problem, troubleOf, waysIn, type Trouble } from '../pages/AuthPages'
import { useAuth } from '../state/auth'
import { sessionExpiredParts } from '../vendor/nexkonten/konten'
import { Dialog } from './Dialog'
import { AccountButton } from './AccountButton'
import { ProviderButtons } from './ProviderButtons'
import { SignOutWaiting, useSignOut } from './SignOut'

/** The steps this dialog shows itself; every other ``next`` lets the page go on (setting a factor up is the app's). */
type Step = 'sign_in' | 'second_factor' | 'passkey'

export function SessionDialog() {
  const { t } = useTranslation()
  const { interrupted, me, resume } = useAuth()
  const leaving = useSignOut()
  const [methods, setMethods] = useState<Methods | null>(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<Step>('sign_in')
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
              <AccountButton name="signOut" testId="session-leave" onClick={leaving.ask}>
                {t('session.leave')}
              </AccountButton>
            </div>
          )}
        </div>
      </Dialog>
    )
  }

  const goOn = async () => {
    setPassword('')
    setCode('')
    setStep('sign_in')
    resume(await authApi.me())
  }

  const attempt = async (work: () => Promise<void>) => {
    setBusy(true)
    setProblem(null)
    try {
      await work()
    } catch (error) {
      const found = troubleOf(error)
      setProblem(found)
      if (found?.code === 'second_factor_expired') setStep('sign_in')
    } finally {
      setBusy(false)
    }
  }

  const submit = () =>
    attempt(async () => {
      if (step === 'second_factor') {
        await authApi.code(code.trim())
        return goOn()
      }
      const answer = await authApi.login(me?.email || me?.name || '', password)
      if ('second_factor' in answer) {
        setPassword('')
        // ``code: false``: the passkey is the only second step (the module's ``next`` = ``passkey``).
        setStep(answer.code === false ? 'passkey' : 'second_factor')
        return
      }
      await goOn()
    })

  const withPasskey = () =>
    attempt(async () => {
      await signInWithPasskey()
      await goOn()
    })

  // ``password`` and its button only where the account has a password and signing in with one is on.
  const withPassword = (methods?.password ?? true) && me?.sign_in !== 'oidc'
  const parts = sessionExpiredParts(step).filter((part) => withPassword || step !== 'sign_in' || (part !== 'password' && part !== 'signInAgain'))
  const here = window.location.pathname + window.location.search
  return (
    <Dialog title={t('account.sessionExpired')} onClose={() => undefined}>
      <form
        className="space-y-4"
        data-testid="session-expired"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <Problem problem={problem} />
        {parts.map((part) => {
          switch (part) {
            case 'sessionExpiredHint':
              return <p key={part} className="text-sm text-mist-300">{t('account.sessionExpiredHint')}</p>
            case 'password':
              return <AuthField key={part} label={t('account.password')} value={password} onChange={setPassword} type="password" autoComplete="current-password" autoFocus />
            case 'code':
              return <AuthField key={part} label={t('auth.code.label')} value={code} onChange={setCode} autoComplete="one-time-code" autoFocus />
            case 'signInAgain':
              return (
                <AccountButton key={part} name="account.signInAgain" type="submit" disabled={busy} wide />
              )
            case 'passkeySignIn':
              return passkeysSupported() ? (
                <AccountButton key={part} name="account.passkeySignIn" disabled={busy} wide onClick={() => void withPasskey()} />
              ) : null
            default:
              return null
          }
        })}
      </form>
      {parts.includes('or') && <ProviderButtons providers={methods?.providers ?? []} next={here} withOr={withPassword} />}
    </Dialog>
  )
}
