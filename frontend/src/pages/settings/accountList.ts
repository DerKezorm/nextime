/** nextime's side of the operator's account list of the shared account module (konten 0.3.9): what its server can do,
 * its own act at its fixed place, and its rows as the module knows them. */
import type { AccountRow } from '../../api/client'
import { avatarUrl, personColor } from '../../lib/avatar'
import type { AccountCaps, ListedAccount, OwnAction } from '../../vendor/nexkonten/konten'

/** What nextime's server can do for the operator (konten 0.3.9, `ACCOUNT_CAPS`): everything of the module. */
export const APP_CAPS: AccountCaps = {
  rename: true,
  roles: true,
  emailRemove: true,
  resetLink: true,
  factorReset: true,
  signOut: true,
  unlink: true,
  blocking: true,
  remove: true,
  restore: true,
}

/** nextime's own act in the dialog "Manage": the storage limit, in "Change" after "Make member". */
export const APP_OWN_ACTIONS: readonly OwnAction[] = [
  { name: 'storageLimit', label: 'accounts.storageLimit', group: 'change', button: 'storageLimit', after: 'account.makeMember' },
]

/** One row of nextime's answer as the module's list knows it (the names of the interface stay). */
export function listedOf(row: AccountRow, ownId: number | undefined): ListedAccount {
  return {
    id: row.id,
    username: row.name,
    displayName: row.display_name,
    email: row.email,
    emailConfirmed: row.email_confirmed,
    emailDuplicate: row.email_duplicate,
    operator: row.role === 'operator',
    self: row.id === ownId,
    hasPassword: row.has_password,
    authenticator: row.two_factor,
    passkeys: row.passkeys ?? 0,
    providers: row.providers.map((provider) => ({ id: provider.id, label: provider.label })),
    blocked: row.blocked,
    blockedAt: row.blocked_at,
    locked: row.locked,
    deletedAt: row.deleted_at,
    purgeAt: row.purge_at,
    lastSeenAt: row.last_seen_at,
    picture: avatarUrl(row.id, row.avatar),
    // The person's own colour (TIME-2 EC-11): the letter stands on it here as everywhere else in nextime.
    color: personColor(row.color ?? row.id),
  }
}
