/**
 * The pages before signing in: the first account (with the setup code from the server's log), signing in (password,
 * then the second factor; or a provider's button, in the order of the sign-in blueprint), accepting an invitation, and
 * setting a new password with the operator's reset link.
 */
import { useEffect, useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { ApiError, authApi, invitesApi, resetApi, type InviteOffer, type Me, type Methods, type SetupState } from '../api/client'
import { Logo } from '../components/Logo'
import { PasswordConfirmField } from '../components/PasswordConfirm'
import { ProviderButtons } from '../components/ProviderButtons'
import { ThemeSwitcher } from '../components/ThemeSwitcher'
import i18n from '../i18n'
import { errorText, signInErrorText } from '../lib/errors'
import { bytes } from '../lib/format'
import { usePasswordConfirm } from '../lib/passwordConfirm'
import { useTitle } from '../lib/title'
import { safeNext, useAuth } from '../state/auth'

export function AuthFrame({ title, text, children, footer }: { title: string; text?: string; children: ReactNode; footer?: ReactNode }) {
  useTitle(title)
  return (
    <div className="nt-scroll flex min-h-dvh flex-col overflow-y-auto">
      <header className="flex items-center justify-between px-5 py-4">
        <Logo withWordmark />
        <ThemeSwitcher />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[6vh] pb-10">
        <div className="w-full max-w-sm">
          <div className="rounded-[16px] border border-ink-700 bg-ink-850 p-6 shadow-xl shadow-black/20">
            <h1 className="text-xl font-bold tracking-tight">{title}</h1>
            {text && <p className="mt-1 text-sm text-mist-500">{text}</p>}
            <div className="mt-5">{children}</div>
          </div>
          {footer && <p className="mt-3 text-center text-xs text-mist-600">{footer}</p>}
        </div>
      </main>
    </div>
  )
}

export function AuthField({ label, value, onChange, type = 'text', autoComplete, autoFocus = false, hint, maxLength, name }: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  autoComplete?: string
  autoFocus?: boolean
  hint?: string
  maxLength?: number
  name?: string
}) {
  const hintId = useId()
  return (
    <div className="text-sm">
      <label className="block">
        <span className="font-medium">{label}</span>
        <input
          type={type}
          name={name}
          value={value}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          maxLength={maxLength}
          aria-describedby={hint ? hintId : undefined}
          onChange={(event) => onChange(event.target.value)}
          className="nt-field mt-1"
        />
      </label>
      {hint && (
        <span id={hintId} className="mt-1 block text-xs text-mist-500">
          {hint}
        </span>
      )}
    </div>
  )
}

function Primary({ children, busy = false }: { children: ReactNode; busy?: boolean }) {
  return (
    <button type="submit" disabled={busy} className="nt-btn nt-btn-accent w-full">
      {children}
    </button>
  )
}

/** What went wrong: the server's code with the values its sentence needs ("at least {{minimum}} characters"). */
export type Trouble = { code: string; values?: Record<string, unknown> } | null

/** The refusal as a `Trouble`, values kept: only the code lost them, and the page showed "{{minimum}}". */
// eslint-disable-next-line react-refresh/only-export-components
export function troubleOf(error: unknown): NonNullable<Trouble> {
  return error instanceof ApiError ? { code: error.code, values: error.values } : { code: 'internal_error' }
}

export function Problem({ problem, fromAddress = false }: { problem: Trouble; fromAddress?: boolean }) {
  if (!problem) return null
  return (
    <p role="alert" className="rounded-[9px] border border-bad-500/30 bg-bad-500/10 px-3 py-2 text-sm text-bad-500">
      {fromAddress ? signInErrorText(problem.code) : errorText(problem.code, problem.values)}
    </p>
  )
}

export function Warning({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="rounded-[9px] border border-warn-500/30 bg-warn-500/10 px-3 py-2 text-sm text-warn-500">
      {children}
    </p>
  )
}

const codeOf = (error: unknown) => troubleOf(error).code

/** How long the pages wait for the ways in before they fall back to the password, as on a failed answer. */
export const WAYS_IN_WAIT_MS = 5000
const PASSWORD_ONLY: Methods = { password: true, providers: [] }

/** The ways in, or the password way when the answer fails or does not come in time. */
// eslint-disable-next-line react-refresh/only-export-components
export function waysIn(): Promise<Methods> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => resolve(PASSWORD_ONLY), WAYS_IN_WAIT_MS)
    authApi.methods().then(
      (methods) => resolve({ ...methods, providers: Array.isArray(methods.providers) ? methods.providers : [] }),
      () => resolve(PASSWORD_ONLY),
    ).finally(() => window.clearTimeout(timer))
  })
}

export function SetupPage() {
  const { t } = useTranslation()
  const { status, setMe } = useAuth()
  const [state, setState] = useState<SetupState | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const again = usePasswordConfirm(password)
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Trouble>(null)
  useEffect(() => {
    authApi.setupState().then(setState, () => undefined)
  }, [])
  if (status === 'loading') return null
  // Set up already: the setup page is the sign-in (TIME-1 EC-4).
  if (status !== 'setup') return <Navigate to={status === 'signedIn' ? '/' : '/login'} replace />
  const min = state?.min_password ?? 12
  // The data folder the server really uses (NEXTIME_DATA_DIR), its free space, the version; what it does not say
  // is left out, never filled in.
  const footer = state
    ? [
        state.data_dir ? t('setup.folder', { path: state.data_dir }) : null,
        state.data_free !== undefined && state.data_free !== null ? t('setup.free', { size: bytes(state.data_free, i18n.language) }) : null,
        t('setup.version', { version: state.version }),
      ]
        .filter(Boolean)
        .join(' · ')
    : undefined
  return (
    <AuthFrame title={t('setup.title')} text={t('setup.text')} footer={footer}>
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault()
          if (!name.trim()) return setProblem({ code: 'name_missing' })
          if (password.length < min) return setProblem({ code: 'password_too_short', values: { minimum: min } })
          setProblem(null)
          if (!again.matches()) return
          setBusy(true)
          try {
            setMe(
              await authApi.setup({
                display_name: name.trim(),
                email: email.trim(),
                password,
                password_confirm: again.confirm,
                code: code.trim(),
                language: i18n.language,
              }),
            )
          } catch (error) {
            setProblem(troubleOf(error))
          } finally {
            setBusy(false)
          }
        }}
      >
        {state?.data_on_share && <Warning>{t('setup.share')}</Warning>}
        {state?.https === false && <Warning>{t('server.httpsMissing')}</Warning>}
        <Problem problem={problem} />
        <AuthField label={t('setup.code')} value={code} onChange={setCode} autoFocus hint={t('setup.codeHint')} />
        <AuthField label={t('auth.name')} value={name} onChange={setName} autoComplete="name" maxLength={80} />
        <AuthField label={t('auth.email')} value={email} onChange={setEmail} type="email" autoComplete="email" />
        <AuthField label={t('auth.password')} value={password} onChange={setPassword} type="password" autoComplete="new-password" hint={t('setup.passwordHint', { count: min })} />
        <PasswordConfirmField value={again.confirm} onChange={again.setConfirm} mismatch={again.mismatch} />
        <Primary busy={busy}>{t('setup.submit')}</Primary>
      </form>
    </AuthFrame>
  )
}

export function LoginPage() {
  const { t } = useTranslation()
  const { status, setMe } = useAuth()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [methods, setMethods] = useState<Methods | null>(null)
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  // After a provider that is not trusted with the second factor the server parked the sign-in: the code step.
  const [step, setStep] = useState<'password' | 'code'>(params.get('step') === 'code' ? 'code' : 'password')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Trouble>(params.get('error') ? { code: params.get('error')! } : null)
  const [fromAddress, setFromAddress] = useState(params.get('error') !== null)
  const [operatorWay, setOperatorWay] = useState(false)
  const [forgot, setForgot] = useState(false)
  const next = safeNext(params.get('next'))
  const ended = params.get('ended') === '1'

  useEffect(() => {
    void waysIn().then(setMethods)
  }, [])

  if (status === 'loading') return null
  if (status === 'setup') return <Navigate to="/setup" replace />
  if (status === 'signedIn') return <Navigate to={next} replace />
  if (methods === null) return null

  const submit = async () => {
    if (step === 'password' && !name.trim()) return setProblem({ code: 'email_missing' })
    if (step === 'password' && !password) return setProblem({ code: 'password_missing' })
    setBusy(true)
    setProblem(null)
    setFromAddress(false)
    try {
      let answer: Me | { second_factor: true }
      if (step === 'code') answer = await authApi.code(code.trim())
      else answer = await authApi.login(name.trim(), password)
      if ('second_factor' in answer) {
        setPassword('')
        setStep('code')
        return
      }
      setMe(await authApi.me())
      navigate(next, { replace: true })
    } catch (error) {
      const found = troubleOf(error)
      setProblem(found)
      if (found.code === 'second_factor_expired') {
        setStep('password')
        setCode('')
      }
    } finally {
      setBusy(false)
    }
  }

  if (step === 'code') {
    return (
      <AuthFrame title={t('auth.code.title')} text={t('auth.code.text')}>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (code.trim()) void submit()
          }}
        >
          <Problem problem={problem} fromAddress={fromAddress} />
          <AuthField label={t('auth.code.label')} value={code} onChange={setCode} autoComplete="one-time-code" autoFocus hint={t('auth.code.hint')} />
          <Primary busy={busy}>{t('auth.login.submit')}</Primary>
          <button
            type="button"
            className="w-full text-center text-xs text-mist-500 hover:text-mist-300"
            onClick={() => {
              void authApi.cancelCode().catch(() => undefined)
              setStep('password')
              setCode('')
              setProblem(null)
            }}
          >
            {t('auth.code.back')}
          </button>
        </form>
      </AuthFrame>
    )
  }

  // Blueprint 04: password form, below the line "or" a button per active provider. With the password sign-in off only
  // the buttons, and small at the bottom the operator's way in with the password.
  const providers = methods.providers
  const passwordShown = methods.password || providers.length === 0 || operatorWay
  const endedNote = ended && <Warning>{t('auth.login.ended')}</Warning>
  return (
    <AuthFrame title={t('auth.login.title')}>
      {passwordShown ? (
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          {endedNote}
          <Problem problem={problem} fromAddress={fromAddress} />
          <AuthField label={t('auth.email')} value={name} onChange={setName} autoComplete="username" autoFocus name="email" />
          <AuthField label={t('auth.password')} value={password} onChange={setPassword} type="password" autoComplete="current-password" />
          <Primary busy={busy}>{t('auth.login.submit')}</Primary>
          <button type="button" className="nt-btn-quiet block text-sm" onClick={() => setForgot(!forgot)} aria-expanded={forgot}>
            {t('auth.login.forgot')}
          </button>
          {forgot && <p className="text-xs text-mist-500">{t('auth.login.forgotText')}</p>}
        </form>
      ) : (
        <div className="space-y-4">
          {endedNote}
          <Problem problem={problem} fromAddress={fromAddress} />
        </div>
      )}
      <ProviderButtons providers={providers} next={next} withOr={passwordShown} />
      {!passwordShown && (
        <button type="button" className="mt-4 w-full text-center text-xs text-mist-500 hover:text-mist-300" onClick={() => setOperatorWay(true)}>
          {t('oidc.login.operator')}
        </button>
      )}
      <p className="mt-5 text-center text-xs text-mist-500">{t('auth.login.noAccount')}</p>
    </AuthFrame>
  )
}

export function InvitePage() {
  const { t } = useTranslation()
  const { token = '' } = useParams()
  const { setMe } = useAuth()
  const navigate = useNavigate()
  const [offer, setOffer] = useState<InviteOffer | null>(null)
  /** Why the link does not hold: expired, or anything else (used, withdrawn, replaced). */
  const [invalid, setInvalid] = useState<'invalid' | 'expired' | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const again = usePasswordConfirm(password)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Trouble>(null)
  const [methods, setMethods] = useState<Methods | null>(null)

  useEffect(() => {
    invitesApi.offer(token).then(setOffer, (error) => setInvalid(codeOf(error) === 'invite_expired' ? 'expired' : 'invalid'))
    void waysIn().then(setMethods)
  }, [token])

  if (invalid) {
    return (
      <AuthFrame title={invalid === 'expired' ? t('auth.invite.expiredTitle') : t('auth.invite.invalidTitle')}>
        <p className="text-sm text-mist-400">{invalid === 'expired' ? t('auth.invite.expiredText') : t('auth.invite.invalidText')}</p>
        <Link to="/login" className="nt-btn nt-btn-ghost mt-4 flex w-full">
          {t('auth.backToLogin')}
        </Link>
      </AuthFrame>
    )
  }
  if (!offer || methods === null) return null
  const text = offer.by ? t('auth.invite.textBy', { by: offer.by }) : t('auth.invite.text')

  if (offer.signed_in_as) {
    return (
      <AuthFrame title={t('auth.invite.title')} text={t('auth.invite.haveAccount', { name: offer.signed_in_as })}>
        <Link to="/" className="nt-btn nt-btn-accent flex w-full">
          {t('auth.invite.toApp')}
        </Link>
      </AuthFrame>
    )
  }

  const passwordWay = offer.password && methods.password
  return (
    <AuthFrame title={t('auth.invite.title')} text={text}>
      {!passwordWay && methods.providers.length === 0 && <p className="text-sm text-mist-400">{t('auth.invite.noWay')}</p>}
      {passwordWay && (
        <form
          className="space-y-4"
          onSubmit={async (event) => {
            event.preventDefault()
            if (!name.trim()) return setProblem({ code: 'name_missing' })
            setProblem(null)
            if (!again.matches()) return
            setBusy(true)
            try {
              setMe(
                await invitesApi.accept(token, {
                  display_name: name.trim(),
                  email: (email ?? offer.email).trim(),
                  password,
                  password_confirm: again.confirm,
                }),
              )
              navigate('/', { replace: true })
            } catch (error) {
              setProblem(troubleOf(error))
            } finally {
              setBusy(false)
            }
          }}
        >
          <Problem problem={problem} />
          <AuthField label={t('auth.name')} value={name} onChange={setName} autoComplete="name" autoFocus maxLength={80} />
          <AuthField label={t('auth.email')} value={email ?? offer.email} onChange={setEmail} type="email" autoComplete="email" />
          <AuthField label={t('auth.password')} value={password} onChange={setPassword} type="password" autoComplete="new-password" hint={t('setup.passwordHint', { count: offer.min_password })} />
          <PasswordConfirmField value={again.confirm} onChange={again.setConfirm} mismatch={again.mismatch} />
          <Primary busy={busy}>{t('auth.invite.submit')}</Primary>
        </form>
      )}
      <ProviderButtons providers={methods.providers} invite={token} withOr={passwordWay} />
    </AuthFrame>
  )
}

/** The operator's reset link (TIME-2 AC-10): the person sets a new password; nobody is signed in by it. */
export function ResetPage() {
  const { t } = useTranslation()
  const { token = '' } = useParams()
  const [offer, setOffer] = useState<{ name: string; min_password: number } | null>(null)
  const [invalid, setInvalid] = useState<'invalid' | 'expired' | null>(null)
  const [password, setPassword] = useState('')
  const again = usePasswordConfirm(password)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [problem, setProblem] = useState<Trouble>(null)

  useEffect(() => {
    resetApi.offer(token).then(setOffer, (error) => setInvalid(codeOf(error) === 'reset_expired' ? 'expired' : 'invalid'))
  }, [token])

  if (invalid || done) {
    const key = done ? 'done' : invalid
    return (
      <AuthFrame title={t(`auth.reset.${key}Title`)}>
        <p className="text-sm text-mist-400">{t(`auth.reset.${key}Text`)}</p>
        <Link to="/login" className={'nt-btn mt-4 flex w-full ' + (done ? 'nt-btn-accent' : 'nt-btn-ghost')}>
          {t('auth.backToLogin')}
        </Link>
      </AuthFrame>
    )
  }
  if (!offer) return null
  return (
    <AuthFrame title={t('auth.reset.title')} text={t('auth.reset.text', { name: offer.name })}>
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault()
          if (password.length < offer.min_password) return setProblem({ code: 'password_too_short', values: { minimum: offer.min_password } })
          setProblem(null)
          if (!again.matches()) return
          setBusy(true)
          try {
            await resetApi.use(token, password, again.confirm)
            setDone(true)
          } catch (error) {
            const found = troubleOf(error)
            if (found.code === 'reset_expired' || found.code === 'reset_invalid') setInvalid(found.code === 'reset_expired' ? 'expired' : 'invalid')
            else setProblem(found)
          } finally {
            setBusy(false)
          }
        }}
      >
        <Problem problem={problem} />
        <AuthField label={t('auth.password')} value={password} onChange={setPassword} type="password" autoComplete="new-password" autoFocus hint={t('setup.passwordHint', { count: offer.min_password })} />
        <PasswordConfirmField value={again.confirm} onChange={again.setConfirm} mismatch={again.mismatch} />
        <Primary busy={busy}>{t('auth.reset.submit')}</Primary>
      </form>
    </AuthFrame>
  )
}
