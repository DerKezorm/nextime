/**
 * Settings, "My account": Profile and Security as the shared account module lays them out (`PAGES`, konten 0.3.6: its
 * parts top to bottom, one under the other, with its headings, hints and buttons), then nextime's own parts after them
 * (`APP_PAGES`: "Sign out" of this browser, TIME-2 AC-9; the providers). The profile says how the account signs in
 * (TIME-2 AC-5, AC-8). Appearance stays nextime's own (light or dark, first day of the week, week numbers, clock and
 * time zone, TIME-3 AC-7; duplicate events, TIME-14 AC-4). Each area of the settings shows its `part`; without one,
 * all of it. Without a connection the settings are read only; "Sign out" stands outside of them (TIME-28 EC-1).
 *
 * Built only from the shared kit (`vendor/nexui`, bauplaene/ui), as nexmail's pages are: every topic a `Box` in its
 * card (the password too), the providers a `Box` with a `Row` each in the card "Security", every button of the module
 * through `AccountButton` (kind and symbol from `BUTTONS`, konten 0.3.8).
 */
import { KeyRound, ShieldCheck, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { api, authApi, type Me, type MyProvider } from '../../api/client'
import { AccountButton } from '../../components/AccountButton'
import { Avatar } from '../../components/Avatar'
import { useOffline } from '../../features/offline/connection'
import { browserLanguage, changeLanguage, languageOptions, type LanguageOption } from '../../i18n'
import { confirmOf } from '../../lib/accountButton'
import { copyText } from '../../lib/copy'
import { errorText, problemText, signInErrorText } from '../../lib/errors'
import { day } from '../../lib/format'
import { usePasswordConfirm } from '../../lib/passwordConfirm'
import { go } from '../../lib/providers'
import { knownZones, zonePlace } from '../../lib/display'
import { useDisplay } from '../../lib/useDisplay'
import { waitingChanges } from '../../state/account'
import { useAuth } from '../../state/auth'
import { SignOutCard, UnconfirmedEmailBox } from '../../vendor/nexkonten/AccountCards'
import { DISPLAY_NAME_MAX, EMAIL_MAX, FIELDS, PAGES, passwordChanged, screenFields, SECOND_FACTOR_STATE, showUnconfirmed, USERNAME_MAX } from '../../vendor/nexkonten/konten'
import { Badge, Box, Card, CheckboxRow, ConfirmDialog, Field, Notice, Row, Rows, SelectField } from '../../vendor/nexui'
import { pagePart } from './accountPages'
import { Notice as Done, PasskeysBox, RemainingAccess, SecondFactorOffNotice, SessionsBox } from './SecurityBoxes'
import { saveAsFile } from './actions'

/** The part of "My account" an area of the settings shows. */
export type AccountPart = 'profile' | 'security'

export function AccountTab({ part }: { part?: AccountPart } = {}) {
  const { t, i18n } = useTranslation()
  const shows = (which: AccountPart) => !part || part === which
  /** "Last changed on …" in the box Password, or that it is not known (konten 0.3.10, `passwordChanged`). */
  const changedLine = (at: string | null | undefined) => {
    const changed = passwordChanged(at)
    return t(changed.text, changed.date ? { day: day(changed.date, i18n.language) } : undefined)
  }
  const { me, setMe, refresh } = useAuth()
  const offline = useOffline()
  const [params] = useSearchParams()
  const [name, setName] = useState(me?.display_name ?? '')
  const [username, setUsername] = useState(me?.name ?? '')
  const [email, setEmail] = useState(me?.email ?? '')
  /** A new username or address asks for the own password first (the shared account module). */
  const [confirming, setConfirming] = useState(false)
  const [own, setOwn] = useState('')
  /** Taking the offered address of a provider asks for the own password first. */
  const [taking, setTaking] = useState(false)
  const [providers, setProviders] = useState<MyProvider[] | null>(null)
  const loadProviders = () => authApi.myProviders().then(setProviders, () => setProviders([]))
  useEffect(() => {
    void loadProviders()
  }, [])
  const [changing, setChanging] = useState(false)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const again = usePasswordConfirm(next)
  const [linkPassword, setLinkPassword] = useState('')
  const [linking, setLinking] = useState<MyProvider | null>(null)
  const linkedSlug = params.get('linked')
  const [linkedShown, setLinkedShown] = useState(!!linkedSlug)
  const linkedLabel = providers?.find((entry) => entry.slug === linkedSlug)?.label
  const [done, setDone] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(params.get('error') ? signInErrorText(params.get('error')!) : null)
  const [dialogProblem, setDialogProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [languages, setLanguages] = useState<LanguageOption[]>([])
  useEffect(() => {
    void languageOptions().then(setLanguages)
  }, [])

  if (!me) return null

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true)
    setProblem(null)
    setDone(null)
    setLinkedShown(false)
    try {
      await action()
      setDone(success)
      await refresh()
      return true
    } catch (error) {
      if (error instanceof Done) {
        setDone(error.message)
        await refresh()
        return true
      }
      setProblem(problemText(error))
      return false
    } finally {
      setBusy(false)
    }
  }

  // The provider's name the server kept with the offer (konten 0.3.4); without one the linked provider's, else a word.
  const offerFrom = me.provider_email_from || (providers?.find((entry) => entry.linked)?.label ?? t('oidc.authentik.step.provider'))
  const takeOffer = (password: string) =>
    run(async () => {
      const saved = await authApi.takeProviderEmail(password)
      setEmail(saved.email)
      setMe(saved)
      if (saved.pending_email) throw new Done(t('account.emailConfirmSent', { email: saved.pending_email }))
    }, t('me.emailTaken'))

  /** The hints under a field of the profile, each joined to it by aria-describedby (konten 0.3.7, `screenFields`). */
  const hintsOf = (field: 'displayName' | 'username' | 'email') =>
    (screenFields('profile').find((entry) => entry.field === field)?.hints ?? []).map((key) => t(key))

  const saveProfile = (password: string) =>
    run(async () => {
      const before = me.pending_email ?? ''
      const saved = await authApi.profile({
        display_name: name,
        email: email.trim(),
        ...(username.trim().toLowerCase() !== me.name ? { name: username.trim() } : {}),
        ...(password ? { password } : {}),
      })
      setName(saved.display_name)
      setUsername(saved.name)
      setEmail(saved.email)
      setMe(saved)
      if (saved.pending_email && saved.pending_email !== before) {
        // With a mail server the new address waits for its link; until then the old one counts.
        throw new Done(t('account.emailConfirmSent', { email: saved.pending_email }))
      }
    }, t('me.profile.saved'))

  const closePassword = () => {
    setChanging(false)
    setCurrent('')
    setNext('')
    again.clear()
    setDialogProblem(null)
  }

  const picture = pagePart('profile', 'picture')
  const lastLinkOf = (provider: MyProvider) => me.sign_in === 'oidc' && provider.linked && (providers ?? []).filter((entry) => entry.linked).length === 1

  return (
    <div className="space-y-5">
      <SecondFactorOffNotice on={me.second_factor_off} />
      {me.new_password_origin && <RemainingAccess onDone={() => void refresh()} />}
      <div aria-live="polite" className="space-y-2 empty:hidden">
        {problem && <Notice tone="danger">{problem}</Notice>}
        {(done || (linkedShown && linkedLabel)) && <Notice tone="ok">{done || t('me.linked', { name: linkedLabel })}</Notice>}
      </div>

      {shows('profile') && (
        <Card id="profile" icon={UserRound} title={t(PAGES.profile.title)}>
          <Box
            title={t(picture.title)}
            hint={t(picture.hint)}
            testId="profile-picture"
            actions={
              <>
                <AccountButton
                  name="account.pictureUpload"
                  accept="image/*"
                  testId="picture"
                  disabled={busy}
                  onFile={(file) => void run(async () => setMe(await authApi.avatar(file)), t('me.picture.saved'))}
                />
                {me.avatar && <AccountButton name="account.pictureRemove" disabled={busy} onClick={() => void run(async () => setMe(await authApi.removeAvatar()), t('me.picture.removed'))} />}
              </>
            }
          >
            <Avatar accountId={me.id} color={me.color} name={me.display_name || me.name} stamp={me.avatar} size={56} />
          </Box>
          {/* One under the other, also on a wide screen (`layout: 'stacked'`). One button saves display name,
              username and email together, never leaving a field (konten 0.3.7, `PAGES.profile.save`); a new username
              or address asks for the password first (`confirmOnSave`). */}
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              const changed = { username: username.trim().toLowerCase() !== me.name, email: email.trim().toLowerCase() !== (me.email ?? '').toLowerCase() }
              const weighty = PAGES.profile.confirmOnSave.some((field) => changed[field])
              // An account with a password confirms a new username or address with it; one through a provider only
              // is asked for a fresh sign-in there by the server.
              if (weighty && me.sign_in === 'password') return setConfirming(true)
              void saveProfile('')
            }}
          >
            <Field label={t(pagePart('profile', 'displayName').title)} value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" maxLength={DISPLAY_NAME_MAX} hints={hintsOf('displayName')} />
            <Field
              label={t(pagePart('profile', 'username').title)}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              maxLength={USERNAME_MAX}
              hints={me.former_name ? [...hintsOf('username'), t('account.formerUsername')] : hintsOf('username')}
            />
            <Field label={t(pagePart('profile', 'email').title)} value={email} onChange={(event) => setEmail(event.target.value)} type="email" autoComplete="email" maxLength={EMAIL_MAX} hints={hintsOf('email')} />
            {me.pending_email && (
              <Box
                title={t('account.emailPending', { email: me.pending_email })}
                testId="email-pending"
                actions={
                  <>
                    <AccountButton name="account.emailResend" disabled={busy} onClick={() => void run(async () => setMe(await authApi.resendEmail()), t('account.emailConfirmSent', { email: me.pending_email }))} />
                    <AccountButton name="account.emailWithdraw" disabled={busy} onClick={() => void run(async () => setMe(await authApi.withdrawEmail()), '')} />
                  </>
                }
              />
            )}
            {showUnconfirmed({ email: me.email, emailConfirmed: me.email_confirmed, emailProven: me.email_proven, pendingEmail: me.pending_email }, !!me.mail_server) && (
              <UnconfirmedEmailBox t={t} email={me.email} errorText={problemText} onSend={async () => setMe(await authApi.proveEmail())} />
            )}
            <div>
              <AccountButton
                name="account.save"
                type="submit"
                disabled={busy || (name.trim() === me.display_name && email.trim() === (me.email ?? '') && username.trim().toLowerCase() === me.name)}
              >
                {t(PAGES.profile.saveButton)}
              </AccountButton>
            </div>
          </form>
          {confirming && (
            <ConfirmDialog
              title={t('account.confirmWithPassword')}
              confirm={confirmOf('account.save', t('account.save'))}
              cancel={t('common.cancel')}
              closeLabel={t('common.close')}
              confirmDisabled={!own}
              onCancel={() => setConfirming(false)}
              onConfirm={() => {
                setConfirming(false)
                void saveProfile(own)
                setOwn('')
              }}
            >
              <Field label={t('account.currentPassword')} value={own} onChange={(event) => setOwn(event.target.value)} type="password" autoComplete="current-password" />
            </ConfirmDialog>
          )}
          {me.provider_email && (
            // The shared account module (0.3.3; the provider's name since 0.3.4): an account with a password is only
            // offered the address and takes it like a change of address, with its password; "No, thanks" holds for
            // this address for good.
            <Box
              title={t(pagePart('profile', 'providerEmailOffer').title, { provider: offerFrom })}
              hint={me.provider_email}
              testId="mail-offer"
              actions={
                <>
                  <AccountButton name="account.providerEmailTake" disabled={busy} onClick={() => (me.sign_in === 'password' ? setTaking(true) : void takeOffer(''))} />
                  <AccountButton name="account.providerEmailDismiss" disabled={busy} onClick={() => void run(() => authApi.declineProviderEmail(), t('me.emailDeclined'))} />
                </>
              }
            />
          )}
          {taking && (
            <ConfirmDialog
              title={t('account.confirmWithPassword')}
              confirm={confirmOf('account.providerEmailTake', t('account.providerEmailTake'))}
              cancel={t('common.cancel')}
              closeLabel={t('common.close')}
              confirmDisabled={!own}
              onCancel={() => setTaking(false)}
              onConfirm={() => {
                setTaking(false)
                void takeOffer(own)
                setOwn('')
              }}
            >
              <Field label={t('account.currentPassword')} value={own} onChange={(event) => setOwn(event.target.value)} type="password" autoComplete="current-password" />
            </ConfirmDialog>
          )}
          <SelectField
            label={t(pagePart('profile', 'language').title)}
            value={me.language}
            hints={[t('me.looks.languageHint')]}
            data-testid="language-select"
            options={[
              { value: '', label: t('me.looks.browser') },
              ...languages.map((option) => ({ value: option.code, label: option.added ? `${option.name} ${t('me.looks.byOperator')}` : option.name })),
            ]}
            onChange={(event) => {
              const code = event.target.value
              void run(async () => {
                const saved = await authApi.language(code)
                setMe(saved)
                await changeLanguage(code || browserLanguage(), !!code)
              }, '')
            }}
          />
          {/* The time zone of the account (plan "Einstellungen", E14): under the language, as a choice; it counts on
              every device (TIME-3 AC-6, AC-7). */}
          <ZoneField />
          <p className="m-0 text-xs leading-4 text-[var(--nexui-text-muted)]" data-testid="profile-role">
            {t(pagePart('profile', 'role').title)}: {t(`roles.${me.role}`)}
          </p>
        </Card>
      )}

      {/* Without a connection the settings are read only; signing out stays, outside of them (TIME-28 EC-1). */}
      {shows('profile') && !offline && <SignOut />}

      {shows('security') && (
        <Card id="security" icon={ShieldCheck} title={t(PAGES.security.title)}>
          {me.second_factor_setup_required && <Notice tone="warn">{t('me.setupFirst')}</Notice>}

          {me.sign_in === 'password' && (
            <Box
              title={t(pagePart('security', 'password').title)}
              hint={changedLine(me.password_changed_at)}
              testId="password-row"
              actions={
                <AccountButton
                  name="account.passwordChange"
                  testId="password-change"
                  onClick={() => {
                    setDone(null)
                    setChanging(true)
                  }}
                />
              }
            />
          )}
          {changing && (
            <ConfirmDialog
              title={t('account.passwordChange')}
              confirm={confirmOf('resetSubmit', t('me.password.submit'))}
              cancel={t('common.cancel')}
              closeLabel={t('common.close')}
              busy={busy}
              error={dialogProblem}
              onCancel={closePassword}
              onConfirm={() => {
                if (!current) return setDialogProblem(errorText('current_password_missing'))
                if (!next) return setDialogProblem(errorText('password_missing'))
                setDialogProblem(null)
                if (!again.matches()) return
                void run(async () => {
                  await authApi.password(current, next, again.confirm)
                }, t('me.password.done')).then((ok) => {
                  if (ok) closePassword()
                })
              }}
            >
              <Field label={t('me.password.current')} value={current} onChange={(event) => setCurrent(event.target.value)} type="password" autoComplete="current-password" />
              <Field label={t('me.password.new')} value={next} onChange={(event) => setNext(event.target.value)} type="password" autoComplete="new-password" hints={[t('setup.passwordHint', { count: me.min_password ?? 12 })]} />
              <Field
                label={t(FIELDS.passwordRepeat.label)}
                name="password_confirm"
                value={again.confirm}
                onChange={(event) => again.setConfirm(event.target.value)}
                type="password"
                autoComplete="new-password"
                error={again.mismatch ? t('errors.password_mismatch') : undefined}
              />
            </ConfirmDialog>
          )}

          <SecondFactor me={me} />

          <PasskeysBox signedInByPassword={me.sign_in === 'password'} />

          <SessionsBox busy={busy} onEverywhere={() => run(() => authApi.logoutEverywhere(), t('me.sessions.done'))} />

          <CheckboxRow
            label={t(pagePart('security', 'newSignInMail').title)}
            hint={t(pagePart('security', 'newSignInMail').hint)}
            checked={me.new_sign_in_mail !== false}
            disabled={busy}
            onChange={(on) => void run(async () => setMe(await authApi.newSignInMail(on)), '')}
          />

          {/* nextime's own parts come after the module's (`pageOrder`): the providers, a box in this card. */}
          {providers !== null && (providers.length > 0 || me.sign_in === 'oidc') && (
            <Box title={t('oidc.account.title')} hint={t('oidc.account.text')} testId="my-providers">
              <Rows label={t('oidc.account.title')}>
                {providers.map((provider) => {
                  // The last link of an account without a password stays: it would lock the account out.
                  const lastLink = lastLinkOf(provider)
                  return (
                    <Row
                      key={provider.slug}
                      icon={KeyRound}
                      name={provider.label}
                      badge={<Badge tone={provider.linked ? 'on' : 'off'}>{provider.linked ? t('oidc.account.linked') : t('oidc.account.notLinked')}</Badge>}
                      note={lastLink ? t('oidc.account.only', { name: provider.label }) : undefined}
                      action={
                        provider.linked ? (
                          !lastLink && (
                            <AccountButton
                              name="oidc.account.unlink"
                              disabled={busy}
                              onClick={() =>
                                void run(async () => {
                                  await authApi.unlink(provider.slug)
                                  await loadProviders()
                                }, t('me.unlinked', { name: provider.label }))
                              }
                            />
                          )
                        ) : (
                          me.sign_in === 'password' && (
                            <AccountButton
                              name="oidc.account.link"
                              disabled={busy}
                              onClick={() => {
                                setLinkPassword('')
                                setDialogProblem(null)
                                setLinking(provider)
                              }}
                            />
                          )
                        )
                      }
                    />
                  )
                })}
              </Rows>
            </Box>
          )}
          {linking && (
            <ConfirmDialog
              title={`${t('oidc.account.link')}: ${linking.label}`}
              text={t('oidc.account.text')}
              confirm={confirmOf('oidc.account.link', t('oidc.account.link'))}
              cancel={t('common.cancel')}
              closeLabel={t('common.close')}
              busy={busy}
              error={dialogProblem}
              confirmDisabled={!linkPassword}
              onCancel={() => setLinking(null)}
              onConfirm={() => {
                setBusy(true)
                setDialogProblem(null)
                authApi.linkStart(linking.slug, linkPassword).then(
                  ({ url }) => go(url),
                  (error) => {
                    setDialogProblem(problemText(error))
                    setBusy(false)
                  },
                )
              }}
            >
              <Field label={t('auth.password')} value={linkPassword} onChange={(event) => setLinkPassword(event.target.value)} type="password" autoComplete="current-password" />
            </ConfirmDialog>
          )}
        </Card>
      )}

    </div>
  )
}

/** Below this many recovery codes the account is told to make new ones. */
const LOW_CODES = 3

type Enrolment = { secret: string; uri: string; qr_svg: string; expires_at?: string | null }

/**
 * Two parts of the module's security page (`PAGES`, konten 0.3.6): the second factor, its state beside the heading
 * ("On"/"Off", `SECOND_FACTOR_STATE`), set up (scan the code, type one code and the password) and remove; then the
 * recovery codes, how many are left, and new ones. A setup that waits for its first code comes back after a reload
 * (`GET /api/auth/totp/setup`, `factor.pending_setup`). Recovery codes are shown once, right after they were made, to
 * copy or save as a file.
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

  useEffect(() => {
    if (me.sign_in !== 'password' || me.two_factor) return
    let live = true
    api<{ waiting: boolean } & Partial<Enrolment>>('/api/auth/totp/setup').then(
      (held) => {
        if (live && held.waiting && held.secret && held.uri && held.qr_svg) setEnrolment({ secret: held.secret, uri: held.uri, qr_svg: held.qr_svg, expires_at: held.expires_at })
      },
      () => undefined,
    )
    return () => {
      live = false
    }
  }, [me.sign_in, me.two_factor])

  const factor = pagePart('security', 'secondFactor')
  const recovery = pagePart('security', 'recoveryCodes')
  const state = SECOND_FACTOR_STATE[(me.factor_state ?? (me.two_factor ? 'active' : 'none')) as keyof typeof SECOND_FACTOR_STATE] ?? SECOND_FACTOR_STATE.none

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
  const left = me.two_factor_recovery_left

  return (
    <>
      <Box
        title={t(factor.title)}
        badge={
          <Badge tone={state === SECOND_FACTOR_STATE.active ? 'on' : 'off'} testId="second-factor-state">
            {t(state)}
          </Badge>
        }
        hint={t(factor.hint)}
        testId="second-factor"
        actions={
          me.sign_in === 'password' && !enrolment ? (
            me.two_factor ? (
              <AccountButton name="account.totpRemove" onClick={() => setAsking('disable')} />
            ) : (
              <AccountButton name="account.totpSetup" disabled={busy} onClick={() => void act(async () => setEnrolment(await api<Enrolment>('/api/auth/totp/begin', { method: 'POST' })))} />
            )
          ) : undefined
        }
      >
        {me.sign_in !== 'password' ? (
          <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t('twofactor.provider')}</p>
        ) : enrolment ? (
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault()
              void act(async () => {
                const result = await api<{ recovery_codes: string[] }>('/api/auth/totp/confirm', { method: 'POST', body: { code: digits.trim(), password } })
                close()
                setCodes(result.recovery_codes)
              })
            }}
          >
            <p className="m-0 text-sm">{t('twofactor.scan')}</p>
            <div className="flex justify-center">
              <img src={'data:image/svg+xml;utf8,' + encodeURIComponent(enrolment.qr_svg)} alt={t('twofactor.qr')} width={196} height={196} className="rounded-[9px] bg-white p-2" />
            </div>
            <p className="m-0 text-xs text-[var(--nexui-text-muted)]">{t('twofactor.secret')}</p>
            <code className="block rounded-[9px] bg-[var(--nexui-field)] px-3 py-2 font-mono text-xs break-all" data-testid="totp-secret">
              {enrolment.secret.replace(/(.{4})/g, '$1 ').trim()}
            </code>
            <Field label={t('twofactor.code')} value={digits} onChange={(event) => setDigits(event.target.value)} autoComplete="one-time-code" autoFocus mono />
            <Field label={t('auth.password')} value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" hints={[t('twofactor.passwordHint')]} />
            {problem && <Notice tone="danger">{problem}</Notice>}
            <div className="flex flex-wrap gap-2">
              <AccountButton name="cancel" onClick={close}>
                {t('common.cancel')}
              </AccountButton>
              <AccountButton name="confirm" type="submit" disabled={busy || digits.trim().length !== 6 || !password}>
                {t('twofactor.confirm')}
              </AccountButton>
            </div>
          </form>
        ) : (
          problem && asking === null && <Notice tone="danger">{problem}</Notice>
        )}
      </Box>
      {asking === 'disable' && (
        <ConfirmDialog
          title={t('account.totpRemove')}
          text={t('twofactor.disableText')}
          confirm={confirmOf('account.totpRemove', t('account.totpRemove'))}
          cancel={t('common.cancel')}
          closeLabel={t('common.close')}
          busy={busy}
          error={problem}
          confirmDisabled={!password}
          onCancel={close}
          onConfirm={() =>
            void act(async () => {
              await api('/api/auth/totp/disable', { method: 'POST', body: { password } })
              close()
              await refresh()
            })
          }
        >
          <Field label={t('auth.password')} value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" />
        </ConfirmDialog>
      )}

      {me.sign_in === 'password' && (me.two_factor || codes) && (
        <Box
          title={t(recovery.title)}
          badge={!codes && <Badge tone={left < LOW_CODES ? 'warn' : 'off'}>{t('account.recoveryLeft', { count: left })}</Badge>}
          hint={t(recovery.hint)}
          testId="recovery-part"
          actions={!codes && <AccountButton name="account.recoveryCodesNew" onClick={() => setAsking('renew')} />}
        >
          {codes ? (
            <div className="flex flex-col gap-3" data-testid="recovery-codes">
              <Notice tone="warn" title={t('twofactor.codesTitle')}>
                {t('twofactor.codesLead')}
              </Notice>
              <ol className="m-0 grid list-none grid-cols-2 gap-2 rounded-[9px] bg-[var(--nexui-field)] px-4 py-3 font-mono text-sm">
                {codes.map((entry) => (
                  <li key={entry}>{entry}</li>
                ))}
              </ol>
              <div className="flex flex-wrap gap-2">
                <AccountButton name="copy" onClick={() => void copyText(codesText).then(setCopied)}>
                  {copied ? t('common.copied') : t('twofactor.codesCopy')}
                </AccountButton>
                <AccountButton name="download" onClick={() => saveAsFile(`nextime-recovery-codes-${me.name}.txt`, new Blob([codesText + '\n'], { type: 'text/plain' }))}>
                  {t('twofactor.codesDownload')}
                </AccountButton>
                <AccountButton
                  name="done"
                  onClick={() => {
                    setCodes(null)
                    setCopied(false)
                    void refresh()
                  }}
                >
                  {t('twofactor.codesDone')}
                </AccountButton>
              </div>
            </div>
          ) : (
            me.two_factor && left < LOW_CODES && <Notice tone="warn">{t('twofactor.lowCodes')}</Notice>
          )}
        </Box>
      )}
      {asking === 'renew' && (
        <ConfirmDialog
          title={t('account.recoveryCodesNew')}
          text={t('twofactor.renewText')}
          confirm={confirmOf('account.recoveryCodesNew', t('account.recoveryCodesNew'))}
          cancel={t('common.cancel')}
          closeLabel={t('common.close')}
          busy={busy}
          error={problem}
          confirmDisabled={!password}
          onCancel={close}
          onConfirm={() =>
            void act(async () => {
              const result = await api<{ recovery_codes: string[] }>('/api/auth/totp/recovery', { method: 'POST', body: { password } })
              close()
              setCodes(result.recovery_codes)
            })
          }
        >
          <Field label={t('auth.password')} value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" />
        </ConfirmDialog>
      )}
    </>
  )
}

/**
 * "Sign out" (TIME-2 AC-9), the one button for it, here in "My account"; on a phone the settings are the same page. The
 * card of the shared account module with its sentence ("Deine Kalender bleiben, wie sie sind."); changes that still
 * wait ask first, as in the session's dialog. A provider's own session stays: only nextime ends.
 */
export function SignOut() {
  const { t } = useTranslation()
  const { me, signOut } = useAuth()
  return <SignOutCard t={t} things={t('settings.thingsKept')} provider={me?.sign_in === 'oidc'} pending={waitingChanges()} onSignOut={signOut} />
}

/** The account's time zone, under the language in the profile (plan "Einstellungen", E14): saved at once. */
function ZoneField() {
  const { t } = useTranslation()
  const { setMe } = useAuth()
  const { display, device } = useDisplay()
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const zones = knownZones()
  return (
    <SelectField
      label={t('display.zone')}
      value={display.time_zone}
      disabled={busy}
      hints={[t('display.zoneHint')]}
      error={problem ?? undefined}
      data-testid="display-zone"
      id="display-zone"
      options={[{ value: '', label: t('display.zoneDevice', { place: zonePlace(device || 'UTC') }) }, ...zones.map((zone) => ({ value: zone, label: zone.replace(/_/g, ' ') }))]}
      onChange={(event) => {
        const zone = event.target.value
        setBusy(true)
        setProblem(null)
        authApi.display({ time_zone: zone }).then(setMe, (error: unknown) => setProblem(problemText(error))).finally(() => setBusy(false))
      }}
    />
  )
}
