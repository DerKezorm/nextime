/** nextime's side of the operator's account list of the shared account module (konten 0.3.9): what its server can do,
 * its own act at its fixed place, and its rows as the module knows them. */
import type { AccountRow } from '../../api/client'
import { avatarUrl, personColor } from '../../lib/avatar'
import { capsOf, type AccountCaps, type ListedAccount, type OwnAction } from '../../vendor/nexkonten/konten'

/** What nextime's server can do for the operator (konten 1.1.0, `capsOf`): everything of the module, the standard
 * functions of every app, and of the module's features none (no storage limit per account since E24, no AI, no
 * Immich). */
export const APP_CAPS: AccountCaps = capsOf('nextime', {
  rename: true,
  emailRemove: true,
  resetLink: true,
  factorReset: true,
  signOut: true,
  unlink: true,
})

/** nextime's own acts in the dialog "Manage": none any more (the storage limit went with konten 1.1.0, E24). */
export const APP_OWN_ACTIONS: readonly OwnAction[] = []

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
