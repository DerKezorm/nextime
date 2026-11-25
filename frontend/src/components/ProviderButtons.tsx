/**
 * The sign-in providers as buttons, in the order of the operator's list, with the line "or" above them: on the sign-in
 * page "Sign in with …", on the invitation page "Continue with …" (the invitation travels along to the provider).
 * Words from the shared sign-in module (`oidc.login.*`, `oidc.invite.button`), the same in every nex app.
 */
import { useTranslation } from 'react-i18next'

import type { ProviderButton } from '../api/client'
import { go, startAddress } from '../lib/providers'
import { OrLine } from '../vendor/nexui'
import { AccountButton } from './AccountButton'

export function ProviderButtons({
  providers,
  invite,
  next,
  withOr = true,
}: {
  providers: ProviderButton[]
  /** The invitation's key: the buttons say "Continue with" and take it along. */
  invite?: string
  /** The page a direct link asked for: the sign-in lands there. */
  next?: string
  /** The line "or" above, when something stands before the buttons. */
  withOr?: boolean
}) {
  const { t } = useTranslation()
  if (providers.length === 0) return null
  return (
    <>
      {withOr && <OrLine>{t('oidc.login.or')}</OrLine>}
      <div className="flex flex-col gap-2" data-testid="provider-buttons">
        {providers.map((provider) => (
          <AccountButton key={provider.slug} name="oidc.login.button" wide onClick={() => go(startAddress(provider.slug, { invite, next }))}>
            {invite ? t('oidc.invite.button', { name: provider.label }) : t('oidc.login.button', { name: provider.label })}
          </AccountButton>
        ))}
      </div>
    </>
  )
}
