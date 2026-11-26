/**
 * The pages before signing in: the first account (with the setup code from the server's log), signing in (username
 * or email and password, then the second factor; a passkey; or a provider's button, in the order of the sign-in
 * blueprint), a forgotten password, accepting an invitation, setting a new password with a link, and confirming a new
 * address. Fields, their order and their words come from the shared account module (`vendor/nexkonten`).
 */
import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { ApiError, authApi, emailLinkApi, invitesApi, resetApi, type InviteOffer, type Me, type Methods, type SetupState } from '../api/client'
import { AccountButton } from '../components/AccountButton'
import { Logo } from '../components/Logo'
import { PasswordConfirmField } from '../components/PasswordConfirm'
import { ProviderButtons } from '../components/ProviderButtons'
import { ThemeSwitcher } from '../components/ThemeSwitcher'
import i18n from '../i18n'
import { errorText, signInErrorText } from '../lib/errors'
import { passkeysSupported, signInWithPasskey } from '../lib/passkeys'
import { bytes } from '../lib/format'
import { usePasswordConfirm } from '../lib/passwordConfirm'
import { useTitle } from '../lib/title'
import { safeNext, useAuth } from '../state/auth'
import {
  DISPLAY_NAME_MAX,
  EMAIL_MAX,
  fieldLabel,
  followUsername,
  normalizeUsername,
  SETUP_PAGE,
  setupNotes,
  STAY_SIGNED_IN_DEFAULT,
  suggestUsername,
  USERNAME_MAX,
  usernameProblem,
} from '../vendor/nexkonten/konten'
import type { ButtonName } from '../vendor/nexkonten/konten'
import { AuthCard, Button, Checkbox, Field, Notice, TextLink } from '../vendor/nexui'
import { UpdateSetupRow } from '../vendor/nexupdates/UpdateSetupRow'

export function AuthFrame({ title, text, children, footer }: { title: string; text?: string; children: ReactNode; footer?: ReactNode }) {
  useTitle(title)
  return (
    <div className="nt-scroll flex min-h-dvh flex-col overflow-y-auto">
      <header className="flex items-center justify-between px-5 py-4">
        <Logo withWordmark />
        <ThemeSwitcher />
      </header>
      <main className="flex flex-1 items-start justify-center px-4 pt-[6vh] pb-10">
        <AuthCard title={title} subtitle={text} footer={footer}>
          {children}
        </AuthCard>
      </main>
    </div>
  )
}

/** A field of the screens before signing in: the shared kit's `Field` (label joined by `htmlFor`, hints by
 * `aria-describedby`), with the value handed over as text. */
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
  return (
    <Field
      label={label}
      type={type}
      name={name}
      value={value}
      autoComplete={autoComplete}
      autoFocus={autoFocus}
      maxLength={maxLength}
      hints={hint ? [hint] : []}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

/** The one button of a screen, as wide as the card: kind and symbol from the module's table (`BUTTONS`). */
function Primary({ name, children, busy = false }: { name: ButtonName; children: ReactNode; busy?: boolean }) {
  return (
    <AccountButton name={name} type="submit" busy={busy} wide>
      {children}
    </AccountButton>
  )
}

/** A way back or on, as wide as the card (a link: the screens are pages of their own). */
function Onward({ to, kind = 'secondary', children }: { to: string; kind?: 'primary' | 'secondary'; children: ReactNode }) {
  return (
    <Button kind={kind} icon={kind === 'primary' ? 'ArrowRight' : 'ArrowLeft'} href={to} wide>
      {children}
    </Button>
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
  return <Notice tone="danger">{fromAddress ? signInErrorText(problem.code) : errorText(problem.code, problem.values)}</Notice>
}

export function Warning({ children }: { children: ReactNode }) {
  return <Notice tone="warn">{children}</Notice>
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

/** The username field follows the display name until somebody types into it (the shared module's suggestion). */
function useUsername(displayName: string, initial = '') {
  const [state, setState] = useState({ touched: initial !== '', username: initial })
  const username = followUsername(state, displayName)
  return {
    username,
    setUsername: (value: string) => setState({ touched: true, username: value }),
  }
}

export function SetupPage() {
  const { t } = useTranslation()
  const { status, setMe } = useAuth()
  const [state, setState] = useState<SetupState | null>(null)
  const [name, setName] = useState('')
  const { username, setUsername } = useUsername(name)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const again = usePasswordConfirm(password)
  const [code, setCode] = useState('')
  // "Nach neuen Versionen sehen", ticked by default (the shared module updates, U1).
  const [updateCheck, setUpdateCheck] = useState(true)
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
    <AuthFrame title={t(SETUP_PAGE.title)} text={t(SETUP_PAGE.subtitle)} footer={footer}>
      <form
        className="flex flex-col gap-4"
        onSubmit={async (event) => {
          event.preventDefault()
          if (!name.trim()) return setProblem({ code: 'display_name_empty' })
          if (usernameProblem(username)) return setProblem({ code: 'username_invalid' })
          if (password.length < min) return setProblem({ code: 'password_too_short', values: { minimum: min } })
          setProblem(null)
          if (!again.matches()) return
          setBusy(true)
          try {
            setMe(
              await authApi.setup({
                display_name: name.trim(),
                name: normalizeUsername(username),
                email: email.trim(),
                password,
                password_confirm: again.confirm,
                code: code.trim(),
                language: i18n.language,
                update_check: updateCheck,
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
        <AuthField label={t('account.setupCode')} value={code} onChange={setCode} autoComplete="off" autoFocus hint={t('account.setupCodeHint')} />
        <AuthField label={t('account.displayName')} value={name} onChange={setName} autoComplete="name" maxLength={DISPLAY_NAME_MAX} hint={t('account.displayNameHint')} />
        <AuthField label={t('account.username')} value={username} onChange={setUsername} autoComplete="username" maxLength={USERNAME_MAX} hint={t('account.usernameHint')} />
        <AuthField label={t('account.email')} value={email} onChange={setEmail} type="email" autoComplete="email" maxLength={EMAIL_MAX} hint={t('account.emailHint')} />
        <AuthField label={t('account.newPassword')} value={password} onChange={setPassword} type="password" autoComplete="new-password" hint={t('setup.passwordHint', { count: min })} />
        <PasswordConfirmField value={again.confirm} onChange={again.setConfirm} mismatch={again.mismatch} />
        <UpdateSetupRow app="nextime" checked={updateCheck} onChange={setUpdateCheck} bell={false} language={i18n.language} />
        {/* No word about the second factor under the password unless one is required (konten 0.3.6): the first
            account comes before any switch of the operator. */}
        {setupNotes({ factorRequired: false }).map((note) => (
          <p key={note} className="m-0 text-sm">
            {t(note)}
          </p>
        ))}
        <Primary name="setupSubmit" busy={busy}>{t('setup.submit')}</Primary>
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
  const [remember, setRemember] = useState(STAY_SIGNED_IN_DEFAULT)
  // After a provider that is not trusted with the second factor the server parked the sign-in: the code step.
  const [step, setStep] = useState<'password' | 'code'>(params.get('step') === 'code' ? 'code' : 'password')
  const [passkeyStep, setPasskeyStep] = useState(false)
  /** False when a passkey is the only second step (no authenticator: there is no code to ask). */
  const [codeAsked, setCodeAsked] = useState(true)
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

  const signedIn = async () => {
    setMe(await authApi.me())
    navigate(next, { replace: true })
  }

  const submit = async () => {
    if (step === 'password' && !name.trim()) return setProblem({ code: 'email_missing' })
    if (step === 'password' && !password) return setProblem({ code: 'password_missing' })
    setBusy(true)
    setProblem(null)
    setFromAddress(false)
    try {
      let answer: Me | { second_factor: true; passkey?: boolean; code?: boolean }
      if (step === 'code') answer = await authApi.code(code.trim())
      else answer = await authApi.login(name.trim(), password, remember)
      if ('second_factor' in answer) {
        setPassword('')
        setPasskeyStep(Boolean(answer.passkey) && passkeysSupported())
        setCodeAsked(answer.code !== false)
        setStep('code')
        return
      }
      await signedIn()
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

  const withPasskey = async () => {
    setBusy(true)
    setProblem(null)
    try {
      await signInWithPasskey()
      await signedIn()
    } catch (error) {
      if (error instanceof ApiError) setProblem(troubleOf(error))
    } finally {
      setBusy(false)
    }
  }

  if (step === 'code') {
    return (
      <AuthFrame title={t('auth.code.title')} text={codeAsked ? t('auth.code.text') : t('account.passkeysHint')}>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (code.trim()) void submit()
          }}
        >
          <Problem problem={problem} fromAddress={fromAddress} />
          {codeAsked && (
            <>
              <AuthField label={t('account.code')} value={code} onChange={setCode} autoComplete="one-time-code" autoFocus hint={t('account.codeHint')} />
              <Primary name="codeSubmit" busy={busy}>{t('auth.login.submit')}</Primary>
            </>
          )}
          {(passkeyStep || !codeAsked) && (
            <AccountButton name="account.passkeySignIn" wide disabled={busy} onClick={() => void withPasskey()} />
          )}
          <div className="text-center">
            <TextLink
              onClick={() => {
                void authApi.cancelCode().catch(() => undefined)
                setStep('password')
                setCode('')
                setProblem(null)
              }}
            >
              {t('auth.code.back')}
            </TextLink>
          </div>
        </form>
      </AuthFrame>
    )
  }

  // Blueprint 04: password form, below the line "or" a button per active provider. With the password sign-in off only
  // the buttons, and small at the bottom the operator's way in with the password.
  const providers = methods.providers
  const passwordShown = methods.password || providers.length === 0 || operatorWay
  const endedNote = ended && <Warning>{t('auth.login.ended')}</Warning>
  const passkeyShown = Boolean(methods.passkeys) && passkeysSupported()
  return (
    <AuthFrame title={t('auth.login.title')}>
      {passwordShown ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          {endedNote}
          <Problem problem={problem} fromAddress={fromAddress} />
          <AuthField label={t('account.login')} value={name} onChange={setName} autoComplete="username" autoFocus name="email" maxLength={EMAIL_MAX} />
          <AuthField label={t('account.password')} value={password} onChange={setPassword} type="password" autoComplete="current-password" />
          <Checkbox label={t('account.stayIn')} checked={remember} onChange={setRemember} />
          <Primary name="signIn" busy={busy}>{t('auth.login.submit')}</Primary>
          <div className="text-center">
            {methods.forgot_mail ? (
              <TextLink href="/forgot">{t('account.forgot')}</TextLink>
            ) : (
              <TextLink onClick={() => setForgot(!forgot)}>{t('account.forgot')}</TextLink>
            )}
          </div>
          {!methods.forgot_mail && forgot && <p className="m-0 text-xs text-[var(--nexui-text-muted)]">{t('account.forgotNoMail')}</p>}
        </form>
      ) : (
        <div className="flex flex-col gap-4">
          {endedNote}
          <Problem problem={problem} fromAddress={fromAddress} />
        </div>
      )}
      {passkeyShown && <AccountButton name="account.passkeySignIn" wide disabled={busy} onClick={() => void withPasskey()} />}
      <ProviderButtons providers={providers} next={next} withOr={passwordShown} />
      {!passwordShown && (
        <div className="text-center">
          <TextLink onClick={() => setOperatorWay(true)}>{t('oidc.login.operator')}</TextLink>
        </div>
      )}
      <p className="m-0 text-center text-xs text-[var(--nexui-text-muted)]">{t('auth.login.noAccount')}</p>
    </AuthFrame>
  )
}

/** "Forgot your password?" by mail: the same answer whatever was typed; without a mail server only the sentence. */
export function ForgotPage() {
  const { t } = useTranslation()
  const [methods, setMethods] = useState<Methods | null>(null)
  const [name, setName] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Trouble>(null)
  useEffect(() => {
    void waysIn().then(setMethods)
  }, [])
  if (methods === null) return null
  const back = <Onward to="/login">{t('auth.backToLogin')}</Onward>
  if (!methods.forgot_mail) {
    return (
      <AuthFrame title={t('account.forgotTitle')}>
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t('account.forgotNoMail')}</p>
        {back}
      </AuthFrame>
    )
  }
  if (sent) {
    return (
      <AuthFrame title={t('account.forgotTitle')}>
        <Notice tone="ok">{t('account.forgotSent')}</Notice>
        {back}
      </AuthFrame>
    )
  }
  return (
    <AuthFrame title={t('account.forgotTitle')}>
      <form
        className="flex flex-col gap-4"
        onSubmit={async (event) => {
          event.preventDefault()
          if (!name.trim()) return
          setBusy(true)
          setProblem(null)
          try {
            await authApi.forgot(name.trim())
            setSent(true)
          } catch (error) {
            setProblem(troubleOf(error))
          } finally {
            setBusy(false)
          }
        }}
      >
        <Problem problem={problem} />
        <AuthField label={t('account.login')} value={name} onChange={setName} autoComplete="username" autoFocus maxLength={EMAIL_MAX} />
        <Primary name="account.forgotSubmit" busy={busy}>{t('account.forgotSubmit')}</Primary>
      </form>
      {back}
    </AuthFrame>
  )
}

/** The link that confirms a new address: one click, no sign-in needed. */
export function EmailPage() {
  const { t } = useTranslation()
  const { token = '' } = useParams()
  const [email, setEmail] = useState<string | null>(null)
  const [state, setState] = useState<'open' | 'done' | 'invalid'>('open')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Trouble>(null)
  useEffect(() => {
    emailLinkApi.offer(token).then((answer) => setEmail(answer.email), () => setState('invalid'))
  }, [token])
  if (state === 'invalid') {
    return (
      <AuthFrame title={t('emailLink.title')}>
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t('account.error.email_link_invalid')}</p>
        <Onward to="/login">{t('auth.backToLogin')}</Onward>
      </AuthFrame>
    )
  }
  if (email === null) return null
  if (state === 'done') {
    return (
      <AuthFrame title={t('emailLink.title')}>
        <Notice tone="ok">{t('account.emailConfirmed')}</Notice>
        <Onward to="/" kind="primary">
          {t('auth.invite.toApp')}
        </Onward>
      </AuthFrame>
    )
  }
  return (
    <AuthFrame title={t('emailLink.title')} text={email}>
      <form
        className="flex flex-col gap-4"
        onSubmit={async (event) => {
          event.preventDefault()
          setBusy(true)
          setProblem(null)
          try {
            await emailLinkApi.confirm(token)
            setState('done')
          } catch (error) {
            setProblem(troubleOf(error))
          } finally {
            setBusy(false)
          }
        }}
      >
        <Problem problem={problem} />
        <Primary name="confirm" busy={busy}>{t('emailLink.submit')}</Primary>
      </form>
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
  const [name, setName] = useState<string | null>(null)
  const [username, setUsernameState] = useState<{ touched: boolean; username: string }>({ touched: false, username: '' })
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
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{invalid === 'expired' ? t('auth.invite.expiredText') : t('auth.invite.invalidText')}</p>
        <Onward to="/login">{t('auth.backToLogin')}</Onward>
      </AuthFrame>
    )
  }
  if (!offer || methods === null) return null
  const text = offer.by ? t('auth.invite.textBy', { by: offer.by }) : t('auth.invite.text')

  if (offer.signed_in_as) {
    return (
      <AuthFrame title={t('auth.invite.title')} text={t('auth.invite.haveAccount', { name: offer.signed_in_as })}>
        <Onward to="/" kind="primary">
          {t('auth.invite.toApp')}
        </Onward>
      </AuthFrame>
    )
  }

  const shown = name ?? offer.display_name ?? ''
  // The suggestion of the server (from the invitation) until the display name is typed, then it follows that.
  const suggested = username.touched ? username.username : (name === null ? (offer.name ?? '') : suggestUsername(shown))
  const passwordWay = offer.password && methods.password
  return (
    <AuthFrame title={t('auth.invite.title')} text={text}>
      {!passwordWay && methods.providers.length === 0 && <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t('auth.invite.noWay')}</p>}
      {passwordWay && (
        <form
          className="flex flex-col gap-4"
          onSubmit={async (event) => {
            event.preventDefault()
            if (!shown.trim()) return setProblem({ code: 'display_name_empty' })
            if (usernameProblem(suggested)) return setProblem({ code: 'username_invalid' })
            setProblem(null)
            if (!again.matches()) return
            setBusy(true)
            try {
              setMe(
                await invitesApi.accept(token, {
                  display_name: shown.trim(),
                  name: normalizeUsername(suggested),
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
          <AuthField label={t('account.displayName')} value={shown} onChange={setName} autoComplete="name" autoFocus maxLength={DISPLAY_NAME_MAX} hint={t('account.displayNameHint')} />
          <AuthField
            label={t('account.username')}
            value={suggested}
            onChange={(value) => setUsernameState({ touched: true, username: value })}
            autoComplete="username"
            maxLength={USERNAME_MAX}
            hint={t('account.usernameHint')}
          />
          <AuthField label={t('account.email')} value={email ?? offer.email} onChange={setEmail} type="email" autoComplete="email" maxLength={EMAIL_MAX} hint={t('account.emailHint')} />
          <AuthField label={t('account.newPassword')} value={password} onChange={setPassword} type="password" autoComplete="new-password" hint={t('setup.passwordHint', { count: offer.min_password })} />
          <PasswordConfirmField value={again.confirm} onChange={again.setConfirm} mismatch={again.mismatch} />
          <Primary name="inviteSubmit" busy={busy}>{t('auth.invite.submit')}</Primary>
        </form>
      )}
      <ProviderButtons providers={methods.providers} invite={token} withOr={passwordWay} />
    </AuthFrame>
  )
}

/** A link for a new password (the person's own, the operator's, or the server's emergency command): the person sets
 * a new password; nobody is signed in by it. */
export function ResetPage() {
  const { t } = useTranslation()
  const { token = '' } = useParams()
  const [offer, setOffer] = useState<{ name: string; username?: string; min_password: number } | null>(null)
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
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t(`auth.reset.${key}Text`)}</p>
        <Onward to="/login" kind={done ? 'primary' : 'secondary'}>
          {t('auth.backToLogin')}
        </Onward>
      </AuthFrame>
    )
  }
  if (!offer) return null
  return (
    <AuthFrame title={t('account.resetTitle')} text={t('auth.reset.text', { name: offer.username || offer.name })}>
      <form
        className="flex flex-col gap-4"
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
        <AuthField label={t(fieldLabel('reset', 'newPassword'))} value={password} onChange={setPassword} type="password" autoComplete="new-password" autoFocus hint={t('setup.passwordHint', { count: offer.min_password })} />
        <PasswordConfirmField value={again.confirm} onChange={again.setConfirm} mismatch={again.mismatch} />
        <Primary name="resetSubmit" busy={busy}>{t('auth.reset.submit')}</Primary>
      </form>
    </AuthFrame>
  )
}
