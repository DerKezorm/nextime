/**
 * Server › Sign-in (plan "Einstellungen", Anmeldung), the whole page from the shared modules: konten's `SignInPage`
 * (the emergency hammer's notice, "Ready for the internet?", the card "Sign-in") with the module oidc's
 * `SignInProviders` after it ("Sign-in providers", "authentik in one step"). nextime hands over its words, its
 * server's answers and how it calls it; order, wording, kinds and sizes come from the modules.
 *
 * The switches go to `/api/server/sign-in` (the module's rules: the password sign-in off only with an active provider,
 * the second factor for everybody on only with an own one, off only with the password); the public address keeps its
 * own way (`PUT /api/settings`, the question when authentik knows the old one). Which points of "Ready for the
 * internet?" are open stays per device (`fold.ready`).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, oidcAdminApi, settingsApi, signInApi, type SignInSettingsView } from '../../api/client'
import { useDevicePref } from '../../lib/devicePref'
import { problemText } from '../../lib/errors'
import type { Readiness } from '../../vendor/nexkonten/konten'
import { SignInPage, type AddressQuestion } from '../../vendor/nexkonten/SignInPage'
import { SignInProviders, type ErrorOf, type OidcAdminCalls, type OidcProviderForm } from '../../vendor/nexoidc/SignInProviders'

const APP = 'nextime'

/** How the provider cards call nextime's routes (the module oidc's under `/api`). */
const CALLS: OidcAdminCalls = {
  providers: () => oidcAdminApi.providers(),
  add: (form: OidcProviderForm) => oidcAdminApi.addProvider(form),
  save: (id, form: OidcProviderForm) => oidcAdminApi.saveProvider(Number(id), form),
  remove: (id) => oidcAdminApi.removeProvider(Number(id)),
  impact: (id, issuer) => oidcAdminApi.providerImpact(Number(id), issuer ?? ''),
  order: (ids) => oidcAdminApi.orderProviders(ids.map(Number)),
  authentik: (url, token, confirm, publicUrl) => oidcAdminApi.authentik(url, token, confirm, publicUrl),
  authentikAddress: () => oidcAdminApi.authentikAddress(),
  authentikSaveAddress: (publicUrl, confirm) => oidcAdminApi.authentikSaveAddress(publicUrl, confirm),
}

/** A refusal as the module oidc reads it: the server's code and the values of the answer. */
// eslint-disable-next-line react-refresh/only-export-components
export const errorOf: ErrorOf = (error) => (error instanceof ApiError ? { code: error.code, values: error.values } : null)

export function SignInArea() {
  const { t } = useTranslation()
  const [view, setView] = useState<SignInSettingsView | null>(null)
  const [readiness, setReadiness] = useState<Readiness | null>(null)
  const [opened, setOpened] = useDevicePref('fold.ready', (raw) => {
    try {
      const value = raw ? (JSON.parse(raw) as unknown) : {}
      return value && typeof value === 'object' ? (value as Record<string, boolean>) : {}
    } catch {
      return {}
    }
  })
  const load = useCallback(async () => {
    const [found, ready] = await Promise.all([signInApi.read(), signInApi.readiness().catch(() => null)])
    setView(found)
    setReadiness(ready)
  }, [])
  useEffect(() => {
    void load().catch(() => undefined)
  }, [load])
  const translate = useMemo(() => (key: string, values?: Record<string, unknown>) => t(key, values), [t])
  if (!view) return null

  const saveAddress = async (url: string, confirm: boolean): Promise<AddressQuestion | null> => {
    try {
      await settingsApi.save({ public_url: url, confirm_address: confirm })
    } catch (error) {
      if (error instanceof ApiError && error.code === 'public_url_change') {
        return { count: Number(error.values.count ?? 0), hand: Number(error.values.hand ?? 0), address: String(error.values.address ?? '') }
      }
      throw error
    }
    await load()
    return null
  }

  return (
    <SignInPage
      t={translate}
      app={APP}
      view={view}
      readiness={readiness}
      publicUrl={view.public_url}
      operatorHasPassword={view.operator_has_password}
      onChange={async (change, password) => {
        setView(await signInApi.save(change, password ?? ''))
        setReadiness(await signInApi.readiness().catch(() => null))
      }}
      onSaveAddress={saveAddress}
      errorText={problemText}
      saved={t('settings.saved')}
      open={opened}
      onOpenChange={(key, open) => setOpened(JSON.stringify({ ...opened, [key]: open }))}
      providers={<SignInProviders t={translate} app={APP} apiPrefix="/api" calls={CALLS} errorOf={errorOf} publicUrl={view.public_url} onAddress={load} />}
    />
  )
}
