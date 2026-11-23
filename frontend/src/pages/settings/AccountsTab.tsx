/**
 * Server › "Accounts" (drawing T2-Konten, TIME-2 AC-1, AC-3, AC-4, AC-6): every person with picture or initial, name,
 * address, role, storage and last sign-in; never a calendar, an event or stored access. An invited person stands in
 * the list as "invited" with "Invitation open" until the account is made. Invite a person, block, delete (30 days to
 * bring it back), give a role, set a limit, a reset link (the person sets the new password), sign out everywhere, take
 * a provider's link. `?invite` in the address opens "Invite a person" at once.
 */
import { Menu } from '../../components/Menu'
import { Avatar } from '../../components/Avatar'
import { MoreHorizontal, UserPlus } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { accountsApi, invitesApi, oidcAdminApi, type AccountRow, type Invite, type LinkedProvider } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { bytes, day, dayTime } from '../../lib/format'
import { ago } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { AllKeysCard } from '../../features/keys/KeysSection'
import { useAuth } from '../../state/auth'
import { Button, Card, Confirm, CopyLink, Feedback, Input, Select, Toggle, useAction } from './ui'

const GB = 1024 ** 3

type Asking =
  | { kind: 'block' | 'unblock' | 'restore' | 'signOut' | 'resetFactor' | 'role'; row: AccountRow }
  | { kind: 'delete'; row: AccountRow; calendars: number; events: number; shared: number; days: number }
  | { kind: 'unlink'; row: AccountRow; provider: LinkedProvider }

export function AccountsTab() {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const [rows, setRows] = useState<AccountRow[] | null>(null)
  const [invites, setInvites] = useState<Invite[]>([])
  const [params, setParams] = useSearchParams()
  const [inviting, setInviting] = useState(() => params.has('invite'))
  const [menu, setMenu] = useState<{ id: number; anchor: HTMLElement } | null>(null)
  const [asking, setAsking] = useState<Asking | null>(null)
  const [limitFor, setLimitFor] = useState<AccountRow | null>(null)
  const [resetFor, setResetFor] = useState<AccountRow | null>(null)
  const action = useAction()
  const lang = i18n.language
  const { timeOptions } = useDisplay()

  const load = useCallback(() => {
    accountsApi.list().then(setRows, () => setRows([]))
    invitesApi.list().then(setInvites, () => undefined)
  }, [])
  useEffect(load, [load])

  if (!rows || !me) return null
  const operatorPassword = me.sign_in === 'password'
  const living = rows.filter((row) => !row.deleted_at)

  return (
    <div className="space-y-6">
      <Card id="accounts" icon={UserPlus} title={t('accounts.title')} text={t('accounts.lead', { count: living.length })}>
        <div className="flex justify-end">
          <Button accent onClick={() => setInviting(true)}>
            <UserPlus className="h-4 w-4" strokeWidth={1.8} aria-hidden />
            {t('accounts.invite')}
          </Button>
        </div>
        <div className="overflow-x-auto rounded-[12px] border border-ink-700">
          <table className="w-full min-w-[640px] text-left text-sm" data-testid="account-table">
            <thead className="border-b border-ink-700 text-xs text-mist-500">
              <tr>
                <th className="px-3 py-2 font-medium">{t('accounts.person')}</th>
                <th className="px-3 py-2 font-medium">{t('accounts.role')}</th>
                <th className="px-3 py-2 font-medium">{t('accounts.storage')}</th>
                <th className="px-3 py-2 font-medium">{t('accounts.last')}</th>
                <th className="px-3 py-2 font-medium">
                  <span className="sr-only">{t('accounts.actions')}</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-700">
              {rows.map((row) => {
                const shown = row.display_name || row.name
                const share = row.storage_limit ? Math.min(100, Math.round((row.storage_used / row.storage_limit) * 100)) : 0
                const out = !!row.deleted_at || row.blocked
                return (
                  <tr key={row.id} data-testid={`account-${row.name}`} className={out ? 'text-mist-500' : ''}>
                    <td className="px-3 py-2.5">
                      <div className="flex items-center gap-2.5">
                        <Avatar accountId={row.id} color={row.color} name={shown} stamp={row.avatar} />
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-mist-100">
                            {shown}
                            {row.deleted_at ? ` (${t('accounts.deleted')})` : row.blocked ? ` (${t('accounts.blocked')})` : ''}
                          </span>
                          <span className="block truncate text-xs text-mist-500">{row.email || row.name}</span>
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2.5">{t(`roles.${row.role}`)}</td>
                    <td className="px-3 py-2.5">
                      <div className="text-xs">
                        {row.storage_limit
                          ? t('accounts.usedOf', { used: bytes(row.storage_used, lang), limit: bytes(row.storage_limit, lang) })
                          : t('accounts.usedNoLimit', { used: bytes(row.storage_used, lang) })}
                      </div>
                      <div className="mt-1 h-1.5 w-28 overflow-hidden rounded-full bg-ink-700">
                        <div className="h-full rounded-full" style={{ width: `${row.storage_limit ? Math.max(2, share) : 2}%`, background: share > 90 ? 'var(--color-warn-500)' : 'var(--color-accent-500)' }} />
                      </div>
                    </td>
                    <td className="px-3 py-2.5 text-xs">
                      {row.deleted_at && row.purge_at ? (
                        <span className="text-bad-500">{t('accounts.goesOn', { day: day(row.purge_at, lang) })}</span>
                      ) : row.blocked && row.blocked_at ? (
                        <span className="text-bad-500">{t('accounts.blockedSince', { day: day(row.blocked_at, lang) })}</span>
                      ) : row.last_seen_at ? (
                        <span title={dayTime(row.last_seen_at, lang, timeOptions)}>{ago(new Date(row.last_seen_at).getTime(), lang)}</span>
                      ) : (
                        t('accounts.never')
                      )}
                    </td>
                    <td className="relative px-3 py-2.5 text-right">
                      <button
                        type="button"
                        aria-label={t('accounts.actionsFor', { name: shown })}
                        aria-expanded={menu?.id === row.id}
                        aria-haspopup="menu"
                        className="nt-btn nt-btn-ghost h-8 w-8 px-0"
                        data-testid="account-menu-button"
                        onClick={(event) => setMenu(menu?.id === row.id ? null : { id: row.id, anchor: event.currentTarget })}
                      >
                        <MoreHorizontal className="h-4 w-4" aria-hidden />
                      </button>
                      {menu?.id === row.id && (
                        <Menu anchor={menu.anchor} onClose={() => setMenu(null)} label={t('accounts.actionsFor', { name: shown })} className="text-left" testId="account-menu">
                          {row.deleted_at ? (
                            <MenuItem onClick={() => setAsking({ kind: 'restore', row })}>{t('accounts.restore')}</MenuItem>
                          ) : (
                            <>
                              <MenuItem onClick={() => setAsking({ kind: 'role', row })}>{row.role === 'operator' ? t('accounts.makeMember') : t('accounts.makeOperator')}</MenuItem>
                              <MenuItem onClick={() => setLimitFor(row)}>{t('accounts.setLimit')}</MenuItem>
                              <MenuItem onClick={() => setResetFor(row)}>{t('accounts.resetLink')}</MenuItem>
                              {row.two_factor && <MenuItem onClick={() => setAsking({ kind: 'resetFactor', row })}>{t('accounts.resetFactor')}</MenuItem>}
                              <MenuItem onClick={() => setAsking({ kind: 'signOut', row })}>{t('accounts.signOut')}</MenuItem>
                              {row.providers.map((provider) => (
                                <MenuItem key={provider.id} onClick={() => setAsking({ kind: 'unlink', row, provider })}>
                                  {t('accounts.unlink', { name: provider.label })}
                                </MenuItem>
                              ))}
                              {row.id !== me.id && (
                                <MenuItem onClick={() => setAsking({ kind: row.blocked ? 'unblock' : 'block', row })}>{row.blocked ? t('accounts.unblock') : t('accounts.block')}</MenuItem>
                              )}
                              {row.id !== me.id && (
                                <MenuItem
                                  danger
                                  onClick={() =>
                                    void action.run(async () => {
                                      const impact = await accountsApi.impact(row.id)
                                      setAsking({ kind: 'delete', row, ...impact })
                                    })
                                  }
                                >
                                  {t('accounts.delete')}
                                </MenuItem>
                              )}
                            </>
                          )}
                        </Menu>
                      )}
                    </td>
                  </tr>
                )
              })}
              {invites.map((invite) => (
                <tr key={`invite-${invite.id}`} data-testid="invite-row">
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2.5">
                      <Avatar accountId={-invite.id} name={invite.email || '?'} />
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-mist-100">{invite.email || t('accounts.inviteNoAddress')}</span>
                        <span className="block truncate text-xs text-mist-500">{t('accounts.until', { day: day(invite.expires_at, lang) })}</span>
                      </span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5">{t('accounts.invited')}</td>
                  <td className="px-3 py-2.5 text-xs text-mist-500">–</td>
                  <td className="px-3 py-2.5 text-xs font-medium text-warn-500" data-testid="invite-open">
                    {t('accounts.inviteOpen')}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <Button
                      small
                      danger
                      onClick={() =>
                        void action.run(async () => {
                          await invitesApi.withdraw(invite.id)
                          load()
                        }, t('accounts.withdrawn'))
                      }
                    >
                      {t('accounts.withdraw')}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-mist-500" data-testid="accounts-privacy">
          {t('accounts.privacy')}
        </p>
        <Feedback problem={action.problem} done={action.done} />
      </Card>

      {inviting && (
        <InviteDialog
          onClose={() => {
            setInviting(false)
            if (params.has('invite')) {
              params.delete('invite')
              setParams(params, { replace: true })
            }
          }}
          onMade={load}
        />
      )}
      {limitFor && <LimitDialog row={limitFor} onClose={() => setLimitFor(null)} onSaved={load} />}
      {resetFor && <ResetLinkDialog row={resetFor} askOwn={operatorPassword} onClose={() => setResetFor(null)} />}
      {asking && (
        <Confirm
          title={t(`accounts.confirm.${asking.kind}.title`, { name: asking.row.display_name || asking.row.name })}
          text={
            asking.kind === 'delete'
              ? t('accounts.confirm.delete.text', {
                  calendars: t('accounts.countCalendars', { count: asking.calendars }),
                  events: t('accounts.countEvents', { count: asking.events }),
                  shared: t('accounts.countShared', { count: asking.shared }),
                  days: asking.days,
                })
              : asking.kind === 'unlink'
                ? t('accounts.confirm.unlink.text', { name: asking.provider.label })
                : asking.kind === 'role'
                  ? t(asking.row.role === 'operator' ? 'accounts.confirm.role.toMember' : 'accounts.confirm.role.toOperator')
                  : t(`accounts.confirm.${asking.kind}.text`)
          }
          confirm={t(`accounts.confirm.${asking.kind}.button`)}
          danger={asking.kind === 'delete' || asking.kind === 'block'}
          password={operatorPassword && asking.kind !== 'signOut'}
          onCancel={() => setAsking(null)}
          onConfirm={async (password) => {
            const row = asking.row
            if (asking.kind === 'delete') await accountsApi.remove(row.id, password)
            if (asking.kind === 'restore') await accountsApi.restore(row.id, password)
            if (asking.kind === 'block') await accountsApi.block(row.id, password)
            if (asking.kind === 'unblock') await accountsApi.unblock(row.id, password)
            if (asking.kind === 'signOut') await accountsApi.signOut(row.id)
            if (asking.kind === 'resetFactor') await accountsApi.resetSecondFactor(row.id, password)
            if (asking.kind === 'role') await accountsApi.role(row.id, row.role === 'operator' ? 'member' : 'operator', password)
            if (asking.kind === 'unlink') await oidcAdminApi.unlinkAccount(row.id, asking.provider.id, password)
            setAsking(null)
            setMenu(null)
            load()
          }}
        />
      )}
      <AllKeysCard />
    </div>
  )
}

function MenuItem({ children, onClick, danger = false }: { children: React.ReactNode; onClick: () => void; danger?: boolean }) {
  return (
    <button type="button" role="menuitem" onClick={onClick} className={'nt-menu-item ' + (danger ? 'text-bad-500 hover:text-bad-500' : '')}>
      <span className="truncate">{children}</span>
    </button>
  )
}

function InviteDialog({ onClose, onMade }: { onClose: () => void; onMade: () => void }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<'member' | 'operator'>('member')
  const [days, setDays] = useState('7')
  const [send, setSend] = useState(false)
  const [made, setMade] = useState<{ link: string; sent: boolean; email: string } | null>(null)
  const action = useAction()
  // The switch shows only with a mail server, and counts only with an address: then the button sends (fourth review).
  const mailing = Boolean(me?.mail) && send && Boolean(email.trim())
  return (
    <Dialog title={t('accounts.invite')} onClose={onClose}>
      {made ? (
        <div className="space-y-3">
          {made.sent && (
            <p role="status" className="text-sm font-medium text-ok-500" data-testid="invite-sent">
              {t('accounts.inviteSent', { email: made.email })}
            </p>
          )}
          <p className="text-sm text-mist-300">{t(made.sent ? 'accounts.inviteLinkToo' : 'accounts.inviteLink')}</p>
          <CopyLink value={made.link} label={t('accounts.invite')} />
          <div className="flex justify-end">
            <button type="button" className="nt-btn nt-btn-accent" onClick={onClose}>
              {t('common.done')}
            </button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            void action.run(async () => {
              const answer = await invitesApi.make({ email: email.trim(), role, days: Number(days), send: mailing, language: i18n.language })
              setMade({ link: answer.link, sent: answer.sent, email: email.trim() })
              onMade()
            })
          }}
        >
          <Input label={t('auth.email')} type="email" value={email} onChange={setEmail} hint={t('accounts.inviteEmailHint')} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Select label={t('accounts.role')} value={role} onChange={setRole} options={[{ value: 'member', label: t('roles.member') }, { value: 'operator', label: t('roles.operator') }]} />
            <Select label={t('accounts.valid')} value={days} onChange={setDays} options={['1', '7', '30'].map((value) => ({ value, label: t('server.days', { count: Number(value) }) }))} />
          </div>
          {me?.mail && <Toggle label={t('accounts.sendMail')} checked={send} onChange={setSend} disabled={!email.trim()} />}
          <Feedback problem={action.problem} />
          <div className="flex justify-end gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
              {t('common.cancel')}
            </button>
            <button type="submit" disabled={action.busy} className="nt-btn nt-btn-accent" data-testid="invite-submit">
              {t(mailing ? 'accounts.inviteSend' : 'accounts.inviteMake')}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}

function LimitDialog({ row, onClose, onSaved }: { row: AccountRow; onClose: () => void; onSaved: () => void }) {
  const { t, i18n } = useTranslation()
  const [value, setValue] = useState(row.storage_limit ? String(Math.round((row.storage_limit / GB) * 10) / 10) : '')
  const action = useAction()
  const shown = row.display_name || row.name
  const save = (limit: number | null) =>
    void action.run(async () => {
      await accountsApi.limit(row.id, limit)
      onSaved()
      onClose()
    })
  return (
    <Dialog title={t('accounts.limitTitle', { name: shown })} onClose={onClose}>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          const gigabytes = Number(value.replace(',', '.'))
          if (!gigabytes || gigabytes <= 0) return
          save(Math.round(gigabytes * GB))
        }}
      >
        <p className="text-sm text-mist-400">{t('accounts.limitText')}</p>
        <p className="text-xs text-mist-500">{t('accounts.limitUsed', { used: bytes(row.storage_used, i18n.language) })}</p>
        <Input label={t('accounts.limitField')} type="number" min={0} value={value} onChange={setValue} />
        <Feedback problem={action.problem} />
        <div className="flex flex-wrap justify-end gap-2">
          {row.storage_limit !== null && (
            <button type="button" className="nt-btn nt-btn-ghost" onClick={() => save(null)}>
              {t('accounts.limitRemove')}
            </button>
          )}
          <button type="submit" disabled={action.busy || !value} className="nt-btn nt-btn-accent">
            {t('accounts.limitSet')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/** The operator never sets another account's password: a one-time link lets the person do it. */
function ResetLinkDialog({ row, askOwn, onClose }: { row: AccountRow; askOwn: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const [days, setDays] = useState('7')
  const [own, setOwn] = useState('')
  const [link, setLink] = useState<string | null>(null)
  const action = useAction()
  const name = row.display_name || row.name
  return (
    <Dialog title={t('accounts.resetLinkFor', { name })} onClose={onClose}>
      {link ? (
        <div className="space-y-3" data-testid="reset-link">
          <p className="text-sm text-mist-300">{t('accounts.resetLinkShown', { name })}</p>
          <CopyLink value={link} label={t('accounts.resetLink')} />
          <div className="flex justify-end">
            <button type="button" className="nt-btn nt-btn-accent" onClick={onClose}>
              {t('common.done')}
            </button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            void action.run(async () => setLink((await accountsApi.resetLink(row.id, Number(days), own)).link))
          }}
        >
          <p className="text-sm text-mist-400">{t('accounts.resetLinkText', { name })}</p>
          <Select label={t('accounts.valid')} value={days} onChange={setDays} options={['1', '7', '30'].map((value) => ({ value, label: t('server.days', { count: Number(value) }) }))} />
          {askOwn && <Input label={t('settings.ownPassword')} type="password" autoComplete="current-password" value={own} onChange={setOwn} />}
          <Feedback problem={action.problem} />
          <div className="flex justify-end gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
              {t('common.cancel')}
            </button>
            <button type="submit" disabled={action.busy || (askOwn && !own)} className="nt-btn nt-btn-accent">
              {t('accounts.resetLinkMake')}
            </button>
          </div>
        </form>
      )}
    </Dialog>
  )
}
