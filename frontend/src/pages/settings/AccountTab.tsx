/**
 * Settings, "My account" (drawing T2-MeinKonto), as in nexnotes: Profile (picture, name, address, the way of signing in
 * and whether the second factor is on, TIME-2 AC-5, AC-8; the language, which travels to every device of the account,
 * TIME-3 AC-1; below it "Sign out" of this browser, TIME-2 AC-9), Security after the blueprint (providers, password,
 * second factor, signing out everywhere), Appearance (light or dark, first day of the week, week numbers, clock and
 * time zone, TIME-3 AC-7; duplicate events, TIME-14 AC-4). Each area of the settings shows its `part`; without one,
 * all of it.
 */
import { KeyRound, Lock, LogOut, Palette, ShieldCheck, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { api, authApi, type Me, type MyProvider } from '../../api/client'
import { Avatar } from '../../components/Avatar'
import { Field, Problem } from '../../components/Field'
import { PasswordConfirmField } from '../../components/PasswordConfirm'
import { SignOutWaiting, useSignOut } from '../../components/SignOut'
import { ThemeSwitcher } from '../../components/ThemeSwitcher'
import { browserLanguage, changeLanguage, languageOptions, type LanguageOption } from '../../i18n'
import { copyText } from '../../lib/copy'
import { errorText, problemText, signInErrorText } from '../../lib/errors'
import { day } from '../../lib/format'
import { usePasswordConfirm } from '../../lib/passwordConfirm'
import { go } from '../../lib/providers'
import { useAuth } from '../../state/auth'
import { DisplayFields } from './DisplayFields'
import { Card, saveAsFile } from './ui'
import { useOffline } from '../../features/offline/connection'

const QUIET = 'nt-btn nt-btn-ghost h-8 px-3 text-xs font-medium'
const LOUD = 'nt-btn nt-btn-accent'
const PLAIN = 'nt-btn nt-btn-ghost'

/** The part of "My account" an area of the settings shows. */
export type AccountPart = 'profile' | 'security' | 'appearance'

export function AccountTab({ part }: { part?: AccountPart } = {}) {
  const { t, i18n } = useTranslation()
  const shows = (which: AccountPart) => !part || part === which
  const { me, setMe, refresh } = useAuth()
  const offline = useOffline()
  const [params] = useSearchParams()
  const [name, setName] = useState(me?.display_name ?? '')
  const [email, setEmail] = useState(me?.email ?? '')
  const [providers, setProviders] = useState<MyProvider[] | null>(null)
  const loadProviders = () => authApi.myProviders().then(setProviders, () => setProviders([]))
  useEffect(() => {
    void loadProviders()
  }, [])
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const again = usePasswordConfirm(next)
  const [linkPassword, setLinkPassword] = useState('')
  const [linking, setLinking] = useState<string | null>(null)
  const linkedSlug = params.get('linked')
  const [linkedShown, setLinkedShown] = useState(!!linkedSlug)
  const linkedLabel = providers?.find((entry) => entry.slug === linkedSlug)?.label
  const [done, setDone] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(params.get('error') ? signInErrorText(params.get('error')!) : null)
  const [busy, setBusy] = useState(false)
  const [languages, setLanguages] = useState<LanguageOption[]>([])
  useEffect(() => {
    void languageOptions().then(setLanguages)
  }, [])

  if (!me) return null

  const linkedNames = (providers ?? []).filter((entry) => entry.linked).map((entry) => entry.label)
  const signInWay =
    me.sign_in === 'oidc' || (linkedNames.length > 0 && me.sign_in !== 'password')
      ? t('me.profile.viaProvider', { name: linkedNames.join(', ') || t('me.profile.provider') })
      : t('me.profile.viaPassword')

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true)
    setProblem(null)
    setDone(null)
    setLinkedShown(false)
    try {
      await action()
      setDone(success)
      await refresh()
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <div aria-live="polite">
        <Problem text={problem} />
        {(done ?? (linkedShown && linkedLabel)) && (
          <p className="rounded-[9px] border border-ok-500/30 bg-ok-500/10 px-3 py-2 text-sm text-ok-500">{done ?? t('me.linked', { name: linkedLabel })}</p>
        )}
      </div>

      {shows('profile') && (
        <Card id="profile" icon={UserRound} title={t('me.profile.title')}>
          <div className="flex items-center gap-3" data-testid="profile-picture">
            <Avatar accountId={me.id} color={me.color} name={me.display_name || me.name} stamp={me.avatar} size={56} />
            <div className="flex flex-wrap gap-2">
              <label className={PLAIN + ' cursor-pointer'}>
                {me.avatar ? t('me.picture.change') : t('me.picture.add')}
                <input
                  type="file"
                  accept="image/*"
                  className="sr-only"
                  data-testid="picture-file"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.target.value = ''
                    if (file) void run(async () => setMe(await authApi.avatar(file)), t('me.picture.saved'))
                  }}
                />
              </label>
              {me.avatar && (
                <button type="button" disabled={busy} className={QUIET} onClick={() => void run(async () => setMe(await authApi.removeAvatar()), t('me.picture.removed'))}>
                  {t('me.picture.remove')}
                </button>
              )}
              <p className="basis-full text-xs text-mist-500">{t('me.picture.hint')}</p>
            </div>
          </div>
          <form
            className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
            onSubmit={(event) => {
              event.preventDefault()
              void run(async () => {
                const saved = await authApi.profile({ display_name: name, email })
                setName(saved.display_name)
                setEmail(saved.email)
                setMe(saved)
              }, t('me.profile.saved'))
            }}
          >
            <Field label={t('auth.name')} value={name} onChange={setName} autoComplete="name" maxLength={80} />
            <Field label={t('auth.email')} value={email} onChange={setEmail} type="email" autoComplete="email" />
            <button type="submit" disabled={busy || (name.trim() === me.display_name && email.trim() === me.email)} className={LOUD}>
              {t('common.save')}
            </button>
          </form>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" data-testid="sign-in-way">
            <span className="text-mist-200">{signInWay}</span>
            <span className={'ml-auto text-xs ' + (me.two_factor ? 'text-ok-500' : 'text-mist-500')} data-testid="second-factor-state">
              {me.sign_in === 'oidc' ? t('me.profile.factorProvider') : me.two_factor ? t('me.profile.factorOn') : t('me.profile.factorOff')}
            </span>
          </div>
          <p className="text-xs text-mist-500">{t('me.profile.role', { role: t(`roles.${me.role}`) })}</p>
          {me.provider_email && (
            <div data-testid="mail-offer" className="rounded-[12px] border border-accent-700/40 bg-[var(--color-accent-wash)] px-3.5 py-3 text-sm">
              <p className="text-mist-200">{t('me.emailOffer', { address: me.provider_email })}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" disabled={busy} className={LOUD} onClick={() => void run(() => authApi.takeProviderEmail(), t('me.emailTaken'))}>
                  {t('me.emailTake')}
                </button>
                <button type="button" disabled={busy} className={PLAIN} onClick={() => void run(() => authApi.declineProviderEmail(), t('me.emailDeclined'))}>
                  {t('me.emailDecline')}
                </button>
              </div>
            </div>
          )}
          <label className="block text-sm">
            <span className="font-medium">{t('me.looks.language')}</span> <span className="text-xs text-mist-500">{t('me.looks.languageHint')}</span>
            <select
              className="nt-field mt-1"
              value={me.language}
              data-testid="language-select"
              onChange={(event) => {
                const code = event.target.value
                void run(async () => {
                  const saved = await authApi.language(code)
                  setMe(saved)
                  await changeLanguage(code || browserLanguage(), !!code)
                }, '')
              }}
            >
              <option value="">{t('me.looks.browser')}</option>
              {languages.map((option) => (
                <option key={option.code} value={option.code}>
                  {option.added ? `${option.name} ${t('me.looks.byOperator')}` : option.name}
                </option>
              ))}
            </select>
          </label>
        </Card>
      )}

      {/* Without a connection the settings are read only; signing out stays, outside of them (TIME-28 EC-1). */}
      {shows('profile') && !offline && <SignOutCard viaProvider={me.sign_in === 'oidc'} />}

      {shows('security') && (
        <Card id="security" icon={ShieldCheck} title={t('me.security.title')}>
          {me.second_factor_setup_required && (
            <p role="note" className="rounded-[9px] border border-warn-500/40 bg-warn-500/10 px-4 py-3 text-sm text-warn-500">
              {t('me.setupFirst')}
            </p>
          )}
          {providers !== null && (providers.length > 0 || me.sign_in === 'oidc') && (
            <ul className="divide-y divide-ink-700 rounded-[12px] border border-ink-700" data-testid="my-providers" aria-label={t('oidc.account.title')}>
              {providers.map((provider) => {
                // The last link of an account without a password stays: it would lock the account out.
                const lastLink = me.sign_in === 'oidc' && provider.linked && providers.filter((entry) => entry.linked).length === 1
                return (
                  <li key={provider.slug} className="px-3 py-2.5 text-sm">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="min-w-0 flex-1 truncate font-medium text-mist-100">{provider.label}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${provider.linked ? 'bg-ok-500/10 text-ok-500' : 'border border-ink-700 text-mist-400'}`}>
                        {provider.linked ? t('oidc.account.linked') : t('oidc.account.notLinked')}
                      </span>
                      {provider.linked && !lastLink && (
                        <button
                          type="button"
                          disabled={busy}
                          className={QUIET}
                          onClick={() =>
                            void run(async () => {
                              await authApi.unlink(provider.slug)
                              await loadProviders()
                            }, t('me.unlinked', { name: provider.label }))
                          }
                        >
                          {t('oidc.account.unlink')}
                        </button>
                      )}
                      {!provider.linked && me.sign_in === 'password' && linking !== provider.slug && (
                        <button type="button" disabled={busy} className={QUIET} onClick={() => setLinking(provider.slug)}>
                          {t('oidc.account.link')}
                        </button>
                      )}
                    </div>
                    {lastLink && <p className="mt-1 text-xs text-mist-500">{t('oidc.account.only', { name: provider.label })}</p>}
                    {linking === provider.slug && (
                      <form
                        className="mt-3 flex flex-wrap items-end gap-2"
                        onSubmit={(event) => {
                          event.preventDefault()
                          setBusy(true)
                          setProblem(null)
                          authApi.linkStart(provider.slug, linkPassword).then(
                            ({ url }) => go(url),
                            (error) => {
                              setProblem(problemText(error))
                              setBusy(false)
                            },
                          )
                        }}
                      >
                        <div className="min-w-0 flex-1">
                          <Field label={t('auth.password')} value={linkPassword} onChange={setLinkPassword} type="password" autoComplete="current-password" />
                        </div>
                        <button type="submit" disabled={busy} className={PLAIN}>
                          {t('oidc.account.link')}
                        </button>
                      </form>
                    )}
                  </li>
                )
              })}
            </ul>
          )}

          {me.sign_in === 'password' && (
            <details className="rounded-[12px] border border-ink-700 px-3 py-2.5 text-sm" data-testid="password-row">
              <summary className="flex cursor-pointer list-none items-center gap-3">
                <KeyRound className="h-4 w-4 text-mist-500" aria-hidden />
                <span className="flex-1 font-medium text-mist-100">{t('auth.password')}</span>
                <span className="text-xs text-mist-500">
                  {me.password_changed_at ? t('me.password.changedAt', { day: day(me.password_changed_at, i18n.language) }) : ''}
                </span>
                <span className={QUIET}>{t('me.password.change')}</span>
              </summary>
              <form
                className="mt-3 space-y-3"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (!current) return setProblem(errorText('current_password_missing'))
                  if (!next) return setProblem(errorText('password_missing'))
                  setProblem(null)
                  if (!again.matches()) return
                  void run(async () => {
                    await authApi.password(current, next, again.confirm)
                    setCurrent('')
                    setNext('')
                    again.clear()
                  }, t('me.password.done'))
                }}
              >
                <Field label={t('me.password.current')} value={current} onChange={setCurrent} type="password" autoComplete="current-password" />
                <Field label={t('me.password.new')} value={next} onChange={setNext} type="password" autoComplete="new-password" hint={t('setup.passwordHint', { count: me.min_password ?? 12 })} />
                <PasswordConfirmField value={again.confirm} onChange={again.setConfirm} mismatch={again.mismatch} labelClassName="font-medium text-mist-100" />
                <button type="submit" disabled={busy} className={LOUD}>
                  {t('me.password.submit')}
                </button>
              </form>
            </details>
          )}

          <div className="rounded-[12px] border border-ink-700 px-3 py-2.5">
            <h3 className="mb-2 text-sm font-medium text-mist-100">{t('twofactor.title')}</h3>
            <SecondFactor me={me} />
          </div>

          <div className="flex flex-wrap items-center gap-3 rounded-[12px] border border-ink-700 px-3 py-2.5 text-sm">
            <Lock className="h-4 w-4 text-mist-500" aria-hidden />
            {/* At least 12rem wide: on a phone the button goes to the next line instead of squeezing the text into a
                column one word wide. */}
            <span className="min-w-0 grow basis-48 text-mist-300">{t('me.sessions.text')}</span>
            <button type="button" disabled={busy} onClick={() => void run(() => authApi.logoutEverywhere(), t('me.sessions.done'))} className={QUIET}>
              {t('me.sessions.submit')}
            </button>
          </div>
        </Card>
      )}

      {shows('appearance') && (
        <Card id="looks" icon={Palette} title={t('me.looks.title')}>
          <div className="text-sm">
            <span className="font-medium">{t('me.looks.appearance')}</span> <span className="text-xs text-mist-500">{t('me.looks.appearanceHint')}</span>
            <div className="mt-1.5">
              <ThemeSwitcher withWords />
            </div>
          </div>
          <DisplayFields />
        </Card>
      )}
    </div>
  )
}

/** Below this many recovery codes the account is told to make new ones. */
const LOW_CODES = 3

type Enrolment = { secret: string; uri: string; qr_svg: string }

/**
 * The second factor of the own account: set up (scan the code, type one code and the password), new recovery codes,
 * turn off. Recovery codes are shown once, right after they were made, to copy or save as a file.
 */
function SecondFactor({ me }: { me: Me }) {
  const { t } = useTranslation()
  const { refresh } = useAuth()
  const [enrolment, setEnrolment] = useState<Enrolment | null>(null)
  const [asking, setAsking] = useState<'disable' | 'renew' | null>(null)
  const [digits, setDigits] = useState('')
  const [password, setPassword] = useState('')
  const [codes, setCodes] = useState<string[] | null>(null)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  if (me.sign_in !== 'password') return <p className="text-sm text-mist-400">{t('twofactor.provider')}</p>

  const close = () => {
    setEnrolment(null)
    setAsking(null)
    setDigits('')
    setPassword('')
    setProblem(null)
  }

  const act = async (work: () => Promise<void>) => {
    setBusy(true)
    setProblem(null)
    try {
      await work()
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      setBusy(false)
    }
  }

  const codesText = (codes ?? []).join('\n')

  return (
    <div className="space-y-3 text-sm" data-testid="second-factor">
      {!enrolment && <Problem text={problem} />}
      {codes ? (
        <div className="space-y-3 rounded-[12px] border border-accent-700/40 bg-[var(--color-accent-wash)] p-3" data-testid="recovery-codes">
          <p className="font-semibold text-mist-100">{t('twofactor.codesTitle')}</p>
          <p className="text-xs text-mist-400">{t('twofactor.codesLead')}</p>
          <ol className="grid grid-cols-2 gap-2 rounded-[9px] bg-ink-950 px-4 py-3 font-mono text-sm text-mist-100">
            {codes.map((entry) => (
              <li key={entry}>{entry}</li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={QUIET} onClick={() => void copyText(codesText).then(setCopied)}>
              {copied ? t('common.copied') : t('twofactor.codesCopy')}
            </button>
            <button type="button" className={QUIET} onClick={() => saveAsFile(`nextime-recovery-codes-${me.name}.txt`, new Blob([codesText + '\n'], { type: 'text/plain' }))}>
              {t('twofactor.codesDownload')}
            </button>
            <button
              type="button"
              className={LOUD}
              onClick={() => {
                setCodes(null)
                setCopied(false)
                void refresh()
              }}
            >
              {t('twofactor.codesDone')}
            </button>
          </div>
        </div>
      ) : enrolment ? (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            void act(async () => {
              const result = await api<{ recovery_codes: string[] }>('/api/auth/totp/confirm', { method: 'POST', body: { code: digits.trim(), password } })
              close()
              setCodes(result.recovery_codes)
            })
          }}
        >
          <p className="text-mist-300">{t('twofactor.scan')}</p>
          <div className="flex justify-center">
            <img src={'data:image/svg+xml;utf8,' + encodeURIComponent(enrolment.qr_svg)} alt={t('twofactor.qr')} width={196} height={196} className="rounded-[9px] bg-white p-2" />
          </div>
          <p className="text-xs text-mist-500">{t('twofactor.secret')}</p>
          <code className="block rounded-[9px] bg-ink-950 px-3 py-2 font-mono text-xs break-all text-mist-200" data-testid="totp-secret">
            {enrolment.secret.replace(/(.{4})/g, '$1 ').trim()}
          </code>
          <Field label={t('twofactor.code')} value={digits} onChange={setDigits} autoComplete="one-time-code" autoFocus />
          <Field label={t('auth.password')} value={password} onChange={setPassword} type="password" autoComplete="current-password" hint={t('twofactor.passwordHint')} />
          <Problem text={problem} />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={close} className={PLAIN}>
              {t('common.cancel')}
            </button>
            <button type="submit" disabled={busy || digits.trim().length !== 6 || !password} className={LOUD}>
              {t('twofactor.confirm')}
            </button>
          </div>
        </form>
      ) : asking ? (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            void act(async () => {
              if (asking === 'disable') {
                await api('/api/auth/totp/disable', { method: 'POST', body: { password } })
                close()
                await refresh()
              } else {
                const result = await api<{ recovery_codes: string[] }>('/api/auth/totp/recovery', { method: 'POST', body: { password } })
                close()
                setCodes(result.recovery_codes)
              }
            })
          }}
        >
          <p className="text-mist-300">{asking === 'disable' ? t('twofactor.disableText') : t('twofactor.renewText')}</p>
          <Field label={t('auth.password')} value={password} onChange={setPassword} type="password" autoComplete="current-password" autoFocus />
          <div className="flex justify-end gap-2">
            <button type="button" onClick={close} className={PLAIN}>
              {t('common.cancel')}
            </button>
            <button type="submit" disabled={busy || !password} className={asking === 'disable' ? 'nt-btn nt-btn-danger' : LOUD}>
              {asking === 'disable' ? t('twofactor.disable') : t('twofactor.renew')}
            </button>
          </div>
        </form>
      ) : me.two_factor ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex items-center gap-1.5 text-mist-200">
            <ShieldCheck className="h-4 w-4 text-ok-500" />
            {t('twofactor.on', { count: me.two_factor_recovery_left })}
          </span>
          <button type="button" className={QUIET} onClick={() => setAsking('renew')}>
            {t('twofactor.renew')}
          </button>
          <button type="button" className={QUIET} onClick={() => setAsking('disable')}>
            {t('twofactor.disable')}
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-mist-400">{t('twofactor.off')}</span>
          <button type="button" disabled={busy} className={LOUD} onClick={() => void act(async () => setEnrolment(await api<Enrolment>('/api/auth/totp/begin', { method: 'POST' })))}>
            {t('twofactor.enable')}
          </button>
        </div>
      )}
      {!codes && me.two_factor && me.two_factor_recovery_left < LOW_CODES && (
        <p role="note" className="rounded-[9px] border border-warn-500/40 bg-warn-500/10 px-3 py-2 text-xs text-warn-500">
          {t('twofactor.lowCodes')}
        </p>
      )}
    </div>
  )
}

/**
 * "Sign out" (TIME-2 AC-9), the one button for it, here in "My account"; on a phone the settings are the same page.
 * Changes that still wait ask first, as in the session's dialog. A provider's own session stays: only nextime ends.
 */
export function SignOutCard({ viaProvider }: { viaProvider: boolean }) {
  const { t } = useTranslation()
  const leaving = useSignOut()
  return (
    <Card id="sign-out" icon={LogOut} title={t('me.signOut.title')} text={t('me.signOut.text')}>
      <div data-testid="sign-out-card" className="space-y-3">
        {viaProvider && <p className="text-xs text-mist-500">{t('me.signOut.provider')}</p>}
        {leaving.waiting > 0 ? (
          <SignOutWaiting count={leaving.waiting} onAnyway={leaving.anyway} onWait={leaving.wait} />
        ) : (
          <button type="button" className={PLAIN} data-testid="sign-out" onClick={leaving.ask}>
            {t('me.signOut.button')}
          </button>
        )}
      </div>
    </Card>
  )
}
