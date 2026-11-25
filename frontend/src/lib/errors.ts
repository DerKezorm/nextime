import { ApiError } from '../api/client'
import i18n from '../i18n'
import { accountErrorKeyOf } from '../vendor/nexkonten/konten'
import { mailErrorKey } from '../vendor/nexpost/post'
import { oidcErrorKey } from '../vendor/nexoidc/oidc'

/** The codes nextime answered with before the shared account module, and the module's names for them (the server's
 * `konten_store.OLD_CODES` the other way round). */
export const OLD_ACCOUNT_CODES: Readonly<Record<string, string>> = {
  invalid_email: 'email_invalid',
  name_taken: 'username_taken',
  invalid_account_name: 'username_invalid',
  totp_code_wrong: 'code_wrong',
  second_factor_expired: 'sign_in_expired',
}

/** The sentence for a server's error code, in the language of the page; an unknown code gets the general one. The
 * codes of the shared account and post modules bring their sentences with the modules (`account.*`, `mail.error.*`),
 * the same in every nex app. */
export function errorText(code: string, values: Record<string, unknown> = {}): string {
  // The module's own code first where the server names it next to an old one (`account_code`, konten 0.3.10).
  const accountCode = typeof values.account_code === 'string' ? values.account_code : null
  const account = accountErrorKeyOf({ code, account_code: accountCode }, OLD_ACCOUNT_CODES)
  if (account !== 'account.error.unknown' && i18n.exists(account)) return i18n.t(account, values)
  const mail = mailErrorKey(code)
  if (mail !== 'mail.error.unknown' && i18n.exists(mail)) return i18n.t(mail, values)
  const key = `errors.${code}`
  if (i18n.exists(key)) return i18n.t(key, values)
  // The fixed codes of sign-in through a provider bring their sentences with the shared module (`oidc.error.*`).
  const signIn = oidcErrorKey(code)
  return signIn ? i18n.t(signIn) : i18n.t('errors.internal_error')
}

/** What a way back from a provider says in the address (`?error=`): a fixed sign-in code in the shared module's
 * words, one of nextime' own as such; never the raw code. */
export function signInErrorText(code: string): string {
  const signIn = oidcErrorKey(code)
  return signIn ? i18n.t(signIn) : errorText(code)
}

/** The sentence for whatever went wrong, with the values the server sent along ("at most {{maximum}} characters"). */
export function problemText(error: unknown): string {
  return error instanceof ApiError ? errorText(error.code, error.values) : errorText('internal_error')
}
