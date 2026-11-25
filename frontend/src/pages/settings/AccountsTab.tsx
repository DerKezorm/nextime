/**
 * Server › "Accounts" (TIME-2 AC-1, AC-3, AC-4, AC-6), the operator's list as the shared account module lays it out
 * (konten 0.3.9, `ACCOUNT_LIST`): one row per account with its picture, name, badges and "username · email", and
 * "Manage" on the right, which opens a dialog with every fact and every act in words (`MANAGE_DIALOG`: change, access,
 * end). Never a calendar, an event or stored access (the sentence under the list says so).
 *
 * What nextime adds sits at the module's fixed places: the storage with its limit as a fact and the button "Storage
 * limit" in "Change" after "Make member" (`OwnButton` with the look `storageLimit`); deleting names the calendars,
 * events and shared calendars that go, then the 30 days to bring it back. The open invitations are the module's card
 * under the list (expired ones stay until withdrawn). Under them stay nextime's own cards: the sentence about what the
 * operator sees, and "Keys of all accounts" (TIME-26). `?invite` in the address opens "Invite a person" at once.
 */
import { KeyRound, Mail, Users } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { accountsApi, invitesApi, oidcAdminApi, type AccountRow, type FactorResetOverview, type Invite } from '../../api/client'
import { AccountButton } from '../../components/AccountButton'
import { AllKeysCard } from '../../features/keys/KeysSection'
import { confirmOf } from '../../lib/accountButton'
import { copyText } from '../../lib/copy'
import { bytes, day, dayTime } from '../../lib/format'
import { useDisplay } from '../../lib/useDisplay'
import { useAuth } from '../../state/auth'
import {
  ACCOUNT_LIST,
  accountActions,
  accountBadges,
  accountCount,
  accountFacts,
  accountLine,
  accountName,
  accountPicture,
  confirmLine,
  DISPLAY_NAME_MAX,
  FACTOR_RESET_DEFAULTS,
  factorResetParts,
  INVITE_DIALOG,
  inviteFields,
  inviteRow,
  linkSubmit,
  MANAGE_CONFIRM,
  MANAGE_DIALOG,
  manageGroups,
  OPEN_INVITES,
  RESET_DAYS,
  RESET_DAYS_DEFAULT,
  RESET_LINK_DIALOG,
  removeLines,
  USERNAME_MAX,
  type AccountAction,
  type BadgeShown,
  type FactShown,
  type ListedAccount,
  type ListedInvite,
} from '../../vendor/nexkonten/konten'
import { Avatar, Badge, Button, ButtonGroup, Card, CheckboxRow, ConfirmDialog, Dialog, Facts, Field, Notice, Row, Rows, SelectField, type IconName } from '../../vendor/nexui'
import { APP_CAPS, APP_OWN_ACTIONS, listedOf } from './accountList'
import { Feedback, useAction } from './ui'

const GB = 1024 ** 3

/** The badges of a row or an invitation, in the kit's look; a day in the app's way of writing it. */
function Badges({ shown }: { shown: BadgeShown[] }) {
  const { t, i18n } = useTranslation()
  return (
    <>
      {shown.map((badge, index) => (
        <Badge key={`${badge.badge}-${index}`} tone={badge.tone}>
          {badge.label ?? t(badge.text, badge.date ? { day: day(badge.date, i18n.language) } : undefined)}
        </Badge>
      ))}
    </>
  )
}

/** The picture of an account: its own, else the letter on the person's colour (as everywhere in nextime). */
function Picture({ row, size }: { row: ListedAccount; size: 32 | 48 }) {
  const picture = accountPicture(row)
  return <Avatar src={picture.src} letter={picture.letter} hue={picture.hue} color={picture.color} size={size} />
}

export function AccountsTab() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const [rows, setRows] = useState<AccountRow[] | null>(null)
  const [invites, setInvites] = useState<Invite[]>([])
  const [params, setParams] = useSearchParams()
  const [inviting, setInviting] = useState(() => params.has('invite'))
  const [managing, setManaging] = useState<number | null>(null)
  const [withdrawing, setWithdrawing] = useState<Invite | null>(null)
  const action = useAction()

  const load = useCallback(() => {
    accountsApi.list().then(setRows, () => setRows([]))
    invitesApi.list().then(setInvites, () => undefined)
  }, [])
  useEffect(load, [load])

  if (!rows || !me) return null
  // The operator's own account first, the others as the server orders them.
  const ordered = [...rows.filter((row) => row.id === me.id), ...rows.filter((row) => row.id !== me.id)]
  const listed = ordered.map((row) => listedOf(row, me.id))
  const open = managing === null ? null : ordered.find((row) => row.id === managing) ?? null

  return (
    <div className="space-y-5">
      <Card id="accounts" icon={Users} title={t(ACCOUNT_LIST.title)} subtitle={t(ACCOUNT_LIST.subtitle, { count: accountCount(listed) })} testId="account-list">
        <div>
          <AccountButton name="account.invite" testId="invite-open" onClick={() => setInviting(true)} />
        </div>
        <Rows label={t(ACCOUNT_LIST.title)} testId="account-rows">
          {listed.map((row) => (
            <Row
              key={row.id}
              testId={`account-${row.username}`}
              lead={<Picture row={row} size={32} />}
              name={accountName(row)}
              badge={<Badges shown={accountBadges(row)} />}
              sub={accountLine(row)}
              action={
                <AccountButton name="account.manage" aria-label={t(ACCOUNT_LIST.manageLabel, { name: accountName(row) })} testId="account-manage" onClick={() => setManaging(Number(row.id))}>
                  {t(ACCOUNT_LIST.manage)}
                </AccountButton>
              }
            />
          ))}
        </Rows>
        <Feedback problem={action.problem} done={action.done} />
      </Card>

      {invites.length > 0 && (
        <Card id="open-invites" icon={Mail} title={t(OPEN_INVITES.title)} testId="open-invites">
          <Rows label={t(OPEN_INVITES.title)}>
            {invites.map((invite) => {
              const shown = inviteRow(inviteOf(invite))
              return (
                <Row
                  key={invite.id}
                  testId="invite-row"
                  icon={Mail}
                  name={shown.name || t(shown.nameText)}
                  badge={<Badges shown={shown.badges} />}
                  sub={shown.sub || undefined}
                  action={<AccountButton name="account.inviteWithdraw" onClick={() => setWithdrawing(invite)} />}
                />
              )
            })}
          </Rows>
        </Card>
      )}

      {/* nextime's own cards under the module's: what the operator sees and never sees, and every account's keys. */}
      <Card id="accounts-privacy-card" icon={KeyRound} title={t('accounts.privacyTitle')} testId="accounts-privacy-card">
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]" data-testid="accounts-privacy">
          {t('accounts.privacy')}
        </p>
      </Card>
      <AllKeysCard />

      {open && (
        <ManageDialog
          row={open}
          listed={listedOf(open, me.id)}
          operatorHasPassword={me.sign_in === 'password'}
          mailServer={Boolean(me.mail)}
          onClose={() => setManaging(null)}
          onChanged={load}
          onGone={() => {
            setManaging(null)
            load()
          }}
        />
      )}
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
      {withdrawing && (
        <ConfirmDialog
          title={t('account.inviteWithdraw')}
          text={t(MANAGE_CONFIRM['account.inviteWithdraw'].text)}
          confirm={confirmOf('account.inviteWithdraw', t('account.inviteWithdraw'))}
          cancel={t('common.cancel')}
          closeLabel={t('common.close')}
          busy={action.busy}
          onCancel={() => setWithdrawing(null)}
          onConfirm={() =>
            void action.run(async () => {
              await invitesApi.withdraw(withdrawing.id)
              setWithdrawing(null)
              load()
            }, t('accounts.withdrawn'))
          }
        />
      )}
    </div>
  )
}

/** An invitation of nextime's answer as the module's list knows it. */
function inviteOf(invite: Invite): ListedInvite {
  return {
    id: invite.id,
    displayName: invite.display_name ?? '',
    email: invite.email,
    role: invite.role,
    expiresAt: invite.expires_at,
    ...(invite.expired !== undefined ? { expired: invite.expired } : {}),
  }
}

/** An act of the dialog waiting for its confirmation: the module's or nextime's own, by its name. */
type Pending = { action: Extract<AccountAction, { type: 'button' }>; impact?: { calendars: number; events: number; shared: number; days: number }; overview?: FactorResetOverview | null }

/** The dialog "Manage" (`MANAGE_DIALOG`): the head with picture, name, badges and line; the facts with nextime's
 * storage; then the groups with their buttons in words. Each act asks as `MANAGE_CONFIRM` says. */
function ManageDialog({ row, listed, operatorHasPassword, mailServer, onClose, onChanged, onGone }: {
  row: AccountRow
  listed: ListedAccount
  operatorHasPassword: boolean
  mailServer: boolean
  onClose: () => void
  onChanged: () => void
  onGone: () => void
}) {
  const { t, i18n } = useTranslation()
  const { timeOptions } = useDisplay()
  const lang = i18n.language
  const name = accountName(listed)
  const action = useAction()
  const [pending, setPending] = useState<Pending | null>(null)
  const [naming, setNaming] = useState<'name' | 'display_name' | null>(null)
  const [resetting, setResetting] = useState(false)
  const [limiting, setLimiting] = useState(false)

  const storage: FactShown = {
    fact: 'storage',
    label: 'accounts.storage',
    raw: row.storage_limit
      ? t('accounts.usedOf', { used: bytes(row.storage_used, lang), limit: bytes(row.storage_limit, lang) })
      : t('accounts.usedNoLimit', { used: bytes(row.storage_used, lang) }),
  }
  const facts = accountFacts(listed, [storage]).map((fact) => ({
    label: t(fact.label),
    value: (
      <>
        {fact.raw ?? (fact.date ? dayTime(fact.date, lang, timeOptions) : fact.text ? t(fact.text, fact.values) : '')}
        {fact.state && <span className="text-[var(--nexui-text-muted)]"> · {t(fact.state)}</span>}
      </>
    ),
    note: fact.note ? t(fact.note) : undefined,
    testId: `fact-${fact.fact}`,
  }))
  const groups = manageGroups(accountActions(listed, APP_CAPS, APP_OWN_ACTIONS))

  const start = (act: Extract<AccountAction, { type: 'button' }>) => {
    const base = act.name.split(':')[0]
    if (base === 'account.changeUsername') return setNaming('name')
    if (base === 'account.changeDisplayName') return setNaming('display_name')
    if (base === 'account.resetLink') return setResetting(true)
    if (base === 'storageLimit') return setLimiting(true)
    if (base === 'account.remove') {
      return void action.run(async () => setPending({ action: act, impact: await accountsApi.impact(row.id) }))
    }
    if (base === 'account.factorReset') {
      return void accountsApi.resetOverview(row.id).then(
        (overview) => setPending({ action: act, overview }),
        () => setPending({ action: act, overview: null }),
      )
    }
    setPending({ action: act })
  }

  return (
    <>
      <Dialog title={t(MANAGE_DIALOG.title, { name })} onClose={onClose} closeLabel={t('common.close')} testId="manage-dialog">
        <div className="flex flex-col gap-4">
          <Rows>
            <Row lead={<Picture row={listed} size={48} />} name={name} badge={<Badges shown={accountBadges(listed)} />} sub={accountLine(listed)} />
          </Rows>
          <Facts items={facts} label={t(MANAGE_DIALOG.title, { name })} testId="manage-facts" />
          {groups.map((group) => (
            <ButtonGroup key={group.group} title={t(group.title)} layout={group.layout} testId={`manage-${group.group}`}>
              {group.actions.map((act) =>
                act.type === 'button' ? (
                  <Button key={act.name} kind={act.kind} icon={act.icon as IconName} disabled={action.busy} onClick={() => start(act)} testId={`manage-${act.name}`}>
                    {t(act.label, act.values)}
                  </Button>
                ) : (
                  <CheckboxRow key={act.name} label={t(act.label)} checked={act.checked} onChange={() => undefined} />
                ),
              )}
            </ButtonGroup>
          ))}
          <Feedback problem={action.problem} done={action.done} />
        </div>
      </Dialog>
      {pending && (
        <ConfirmAct
          row={row}
          name={name}
          pending={pending}
          operatorHasPassword={operatorHasPassword}
          onCancel={() => setPending(null)}
          onDone={(gone) => {
            setPending(null)
            if (gone) onGone()
            else onChanged()
          }}
        />
      )}
      {naming && <NameDialog row={row} field={naming} operatorHasPassword={operatorHasPassword} onClose={() => setNaming(null)} onSaved={onChanged} />}
      {resetting && <ResetLinkDialog row={row} name={name} mailServer={mailServer} operatorHasPassword={operatorHasPassword} onClose={() => setResetting(false)} />}
      {limiting && <LimitDialog row={row} name={name} operatorHasPassword={operatorHasPassword} onClose={() => setLimiting(false)} onSaved={onChanged} />}
    </>
  )
}

/** The question of one act: the act's own words, kind and symbol on the confirming button, the module's sentence
 * (`MANAGE_CONFIRM`), for deleting nextime's numbers and how long it can come back, for the reset of the second factor
 * its parts (`factorResetParts`); with the operator's own password where it asks for one (`confirmLine`). */
function ConfirmAct({ row, name, pending, operatorHasPassword, onCancel, onDone }: {
  row: AccountRow
  name: string
  pending: Pending
  operatorHasPassword: boolean
  onCancel: () => void
  onDone: (gone: boolean) => void
}) {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')
  const [disconnect, setDisconnect] = useState<boolean>(FACTOR_RESET_DEFAULTS.factorResetDevices)
  const [removePasskeys, setRemovePasskeys] = useState<boolean>(FACTOR_RESET_DEFAULTS.factorResetPasskeys)
  const action = useAction()
  const act = pending.action
  const base = act.name.split(':')[0] as keyof typeof MANAGE_CONFIRM
  const spec = MANAGE_CONFIRM[base]
  const asksPassword = (spec?.confirm ?? 'password') === 'password'
  const values = { name, provider: act.values?.provider ?? '', email: row.email, app: 'nextime' }
  const passkeys = pending.overview?.passkeys ?? []

  const lines: ReactNode[] = []
  if (spec?.text && base !== 'account.factorReset') lines.push(<p key="text" className="m-0 text-sm">{t(spec.text, values)}</p>)
  if (base === 'account.remove' && pending.impact) {
    lines.push(
      <p key="numbers" className="m-0 text-sm" data-testid="remove-numbers">
        {t('accounts.removeNumbers', {
          calendars: t('accounts.countCalendars', { count: pending.impact.calendars }),
          events: t('accounts.countEvents', { count: pending.impact.events }),
          shared: t('accounts.countShared', { count: pending.impact.shared }),
        })}
      </p>,
    )
    for (const line of removeLines(APP_CAPS)) {
      lines.push(<p key={line} className="m-0 text-sm">{t(line, { count: pending.impact.days })}</p>)
    }
  }
  if (base === 'account.factorReset') {
    for (const part of factorResetParts({ passkeys })) {
      if (part === 'factorResetHint') lines.push(<p key={part} className="m-0 text-sm">{t('account.factorResetHint')}</p>)
      if (part === 'passkeys') lines.push(<p key={part} className="m-0 text-sm">{t('account.passkeys')}: {passkeys.map((key) => key.name).join(', ')}</p>)
      if (part === 'factorResetPasskeys') lines.push(<CheckboxRow key={part} label={t('account.factorResetPasskeys')} checked={removePasskeys} onChange={setRemovePasskeys} />)
      if (part === 'factorResetDevices') lines.push(<CheckboxRow key={part} label={t('account.factorResetDevices')} checked={disconnect} onChange={setDisconnect} />)
    }
  }
  if (asksPassword) lines.push(<p key="confirm" className="m-0 text-sm text-[var(--nexui-text-muted)]">{t(confirmLine(operatorHasPassword))}</p>)

  return (
    <ConfirmDialog
      title={t(act.label, act.values)}
      confirm={confirmOf(act.button, t(act.label, act.values))}
      cancel={t('common.cancel')}
      closeLabel={t('common.close')}
      busy={action.busy}
      confirmDisabled={asksPassword && operatorHasPassword && !password}
      onCancel={onCancel}
      onConfirm={() =>
        void action.run(async () => {
          let gone = false
          if (base === 'account.makeOperator') await accountsApi.role(row.id, 'operator', password)
          if (base === 'account.makeMember') await accountsApi.role(row.id, 'member', password)
          if (base === 'account.emailRemove') await accountsApi.removeEmail(row.id, password)
          if (base === 'account.factorReset') await accountsApi.resetSecondFactor(row.id, password, disconnect, removePasskeys && passkeys.length > 0)
          if (base === 'account.signOutAccount') await accountsApi.signOut(row.id)
          if (base === 'account.unlinkProvider' && act.provider) await oidcAdminApi.unlinkAccount(row.id, Number(act.provider.id), password)
          if (base === 'account.block') await accountsApi.block(row.id, password)
          if (base === 'account.unblock') await accountsApi.unblock(row.id, password)
          if (base === 'account.restore') await accountsApi.restore(row.id, password)
          if (base === 'account.remove') {
            await accountsApi.remove(row.id, password)
            gone = true
          }
          onDone(gone)
        })
      }
    >
      {lines}
      {asksPassword && operatorHasPassword && (
        <Field label={t('account.currentPassword')} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
      )}
      <Feedback problem={action.problem} />
    </ConfirmDialog>
  )
}

/** "Change display name" or "Change username" (the module's fields and hints), with the operator's own password. */
function NameDialog({ row, field, operatorHasPassword, onClose, onSaved }: {
  row: AccountRow
  field: 'name' | 'display_name'
  operatorHasPassword: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const [value, setValue] = useState(field === 'name' ? row.name : row.display_name)
  const [own, setOwn] = useState('')
  const action = useAction()
  const title = field === 'name' ? t('account.changeUsername') : t('account.changeDisplayName')
  return (
    <Dialog title={title} onClose={onClose} closeLabel={t('common.close')}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          void action.run(async () => {
            if (field === 'name') await accountsApi.rename(row.id, value.trim(), own)
            else await accountsApi.displayName(row.id, value.trim(), own)
            onSaved()
            onClose()
          })
        }}
      >
        {field === 'name' && <p className="m-0 text-sm">{t('account.askChangeUsername', { name: row.display_name || row.name })}</p>}
        <Field
          label={field === 'name' ? t('account.username') : t('account.displayName')}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          maxLength={field === 'name' ? USERNAME_MAX : DISPLAY_NAME_MAX}
          hints={[field === 'name' ? t('account.usernameRules') : t('account.displayNameHint')]}
        />
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t(confirmLine(operatorHasPassword))}</p>
        {operatorHasPassword && <Field label={t('account.currentPassword')} type="password" autoComplete="current-password" value={own} onChange={(event) => setOwn(event.target.value)} />}
        <Feedback problem={action.problem} />
        <div className="flex flex-wrap justify-end gap-2">
          <AccountButton name="cancel" onClick={onClose}>
            {t('common.cancel')}
          </AccountButton>
          <AccountButton name="account.save" type="submit" disabled={action.busy || (operatorHasPassword && !own) || (field === 'name' && !value.trim())} />
        </div>
      </form>
    </Dialog>
  )
}

/** A link to hand on: the field to read it in and the button that copies it. */
function CopyField({ value, label, hint }: { value: string; label: string; hint?: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-end gap-2">
      <div className="min-w-0 flex-1">
        <Field label={label} value={value} readOnly mono hints={hint ? [hint] : undefined} onFocus={(event) => event.target.select()} />
      </div>
      <AccountButton name="copy" onClick={() => void copyText(value).then(setCopied)}>
        {copied ? t('common.copied') : t('common.copy')}
      </AccountButton>
    </div>
  )
}

/** "Link for a new password" (`RESET_LINK_DIALOG`): the operator never sets another account's password; with a mail
 * server and an address that counts the link goes there, else it shows once to hand on. */
function ResetLinkDialog({ row, name, mailServer, operatorHasPassword, onClose }: {
  row: AccountRow
  name: string
  mailServer: boolean
  operatorHasPassword: boolean
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [days, setDays] = useState(String(RESET_DAYS_DEFAULT))
  const [own, setOwn] = useState('')
  const [link, setLink] = useState<string | null>(null)
  const [sent, setSent] = useState(false)
  const action = useAction()
  const sends = mailServer && Boolean(row.email) && row.email_confirmed !== false
  return (
    <Dialog title={t(RESET_LINK_DIALOG.title)} onClose={onClose} closeLabel={t('common.close')} testId="reset-link-dialog">
      {sent ? (
        <div className="flex flex-col gap-3" data-testid="reset-link">
          <Notice tone="ok">{t(RESET_LINK_DIALOG.sent, { email: row.email, name })}</Notice>
          <div className="flex justify-end">
            <AccountButton name="done" onClick={onClose}>
              {t('common.done')}
            </AccountButton>
          </div>
        </div>
      ) : link ? (
        <div className="flex flex-col gap-3" data-testid="reset-link">
          <p className="m-0 text-sm">{t(RESET_LINK_DIALOG.shown, { name })}</p>
          <CopyField value={link} label={t(RESET_LINK_DIALOG.title)} />
          <div className="flex justify-end">
            <AccountButton name="done" onClick={onClose}>
              {t('common.done')}
            </AccountButton>
          </div>
        </div>
      ) : (
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            void action.run(async () => {
              const answer = await accountsApi.resetLink(row.id, Number(days), own)
              if (answer.sent) setSent(true)
              else setLink(answer.link)
            })
          }}
        >
          <p className="m-0 text-sm">{t(RESET_LINK_DIALOG.text, { name })}</p>
          <SelectField
            label={t(RESET_LINK_DIALOG.days.label)}
            value={days}
            onChange={(event) => setDays(event.target.value)}
            options={RESET_DAYS.map((value) => ({ value: String(value), label: t('account.days', { count: value }) }))}
          />
          <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t(confirmLine(operatorHasPassword))}</p>
          {operatorHasPassword && <Field label={t('account.currentPassword')} type="password" autoComplete="current-password" value={own} onChange={(event) => setOwn(event.target.value)} />}
          <Feedback problem={action.problem} />
          <div className="flex flex-wrap justify-end gap-2">
            <AccountButton name="cancel" onClick={onClose}>
              {t('common.cancel')}
            </AccountButton>
            <AccountButton name={linkSubmit(sends)} type="submit" disabled={action.busy || (operatorHasPassword && !own)} />
          </div>
        </form>
      )}
    </Dialog>
  )
}

/** nextime's own act: the storage limit of the account (attachments stop at it, writing goes on), with the
 * operator's own password (konten 0.3.9: everything that changes an account asks for it). */
function LimitDialog({ row, name, operatorHasPassword, onClose, onSaved }: {
  row: AccountRow
  name: string
  operatorHasPassword: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const { t, i18n } = useTranslation()
  const [value, setValue] = useState(row.storage_limit ? String(Math.round((row.storage_limit / GB) * 10) / 10) : '')
  const [own, setOwn] = useState('')
  const action = useAction()
  const save = (limit: number | null) =>
    void action.run(async () => {
      await accountsApi.limit(row.id, limit, own)
      onSaved()
      onClose()
    })
  return (
    <Dialog title={t('accounts.limitTitle', { name })} onClose={onClose} closeLabel={t('common.close')} testId="limit-dialog">
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          const gigabytes = Number(value.replace(',', '.'))
          if (!gigabytes || gigabytes <= 0) return
          save(Math.round(gigabytes * GB))
        }}
      >
        <p className="m-0 text-sm">{t('accounts.limitText')}</p>
        <p className="m-0 text-xs text-[var(--nexui-text-muted)]">{t('accounts.limitUsed', { used: bytes(row.storage_used, i18n.language) })}</p>
        <Field label={t('accounts.limitField')} type="number" min={0} value={value} onChange={(event) => setValue(event.target.value)} />
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]">{t(confirmLine(operatorHasPassword))}</p>
        {operatorHasPassword && <Field label={t('account.currentPassword')} type="password" autoComplete="current-password" value={own} onChange={(event) => setOwn(event.target.value)} />}
        <Feedback problem={action.problem} />
        <div className="flex flex-wrap justify-end gap-2">
          {row.storage_limit !== null && (
            <AccountButton name="removeValue" disabled={action.busy || (operatorHasPassword && !own)} onClick={() => save(null)}>
              {t('accounts.limitRemove')}
            </AccountButton>
          )}
          <AccountButton name="storageLimit" type="submit" disabled={action.busy || !value || (operatorHasPassword && !own)}>
            {t('accounts.limitSet')}
          </AccountButton>
        </div>
      </form>
    </Dialog>
  )
}

/** "Invite a person" (`INVITE_DIALOG`): the address, the display name, the role, how long it holds, "Send by mail"
 * only with a mail server and an address; afterwards always the link to copy. No username: the person picks it. */
function InviteDialog({ onClose, onMade }: { onClose: () => void; onMade: () => void }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState<'member' | 'operator'>(INVITE_DIALOG.role.default)
  const [days, setDays] = useState(String(INVITE_DIALOG.days.default))
  const [send, setSend] = useState(true)
  const [made, setMade] = useState<{ link: string; sent: boolean; email: string; expires: string } | null>(null)
  const action = useAction()
  const fields = inviteFields(APP_CAPS, { mailServer: Boolean(me?.mail), email })
  const sends = fields.includes('sendMail') && send
  return (
    <Dialog title={t(INVITE_DIALOG.title)} onClose={onClose} closeLabel={t('common.close')} testId="invite-dialog">
      {made ? (
        <div className="flex flex-col gap-3">
          {made.sent && (
            <Notice tone="ok" testId="invite-sent">
              {t(INVITE_DIALOG.done.sent, { email: made.email })}
            </Notice>
          )}
          <CopyField value={made.link} label={t(INVITE_DIALOG.done.link)} hint={t(INVITE_DIALOG.done.linkHint, { day: day(made.expires, i18n.language) })} />
          <div className="flex justify-end">
            <AccountButton name="done" onClick={onClose}>
              {t('common.done')}
            </AccountButton>
          </div>
        </div>
      ) : (
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            void action.run(async () => {
              const answer = await invitesApi.make({ email: email.trim(), display_name: displayName.trim(), role, days: Number(days), send: sends, language: i18n.language })
              setMade({ link: answer.link, sent: answer.sent, email: email.trim(), expires: answer.expires_at })
              onMade()
            })
          }}
        >
          {fields.includes('email') && <Field label={t('account.email')} type="email" value={email} onChange={(event) => setEmail(event.target.value)} hints={[t(INVITE_DIALOG.emailHint)]} />}
          {fields.includes('displayName') && <Field label={t('account.displayName')} value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={DISPLAY_NAME_MAX} />}
          {fields.includes('role') && (
            <SelectField
              label={t(INVITE_DIALOG.role.label)}
              value={role}
              onChange={(event) => setRole(event.target.value as 'member' | 'operator')}
              options={INVITE_DIALOG.role.options.map((value) => ({ value, label: t(INVITE_DIALOG.role.texts[value]) }))}
            />
          )}
          {fields.includes('days') && (
            <SelectField
              label={t(INVITE_DIALOG.days.label)}
              value={days}
              onChange={(event) => setDays(event.target.value)}
              options={INVITE_DIALOG.days.options.map((value) => ({ value: String(value), label: t('account.days', { count: value }) }))}
            />
          )}
          {fields.includes('sendMail') && <CheckboxRow label={t(INVITE_DIALOG.sendMail)} checked={send} onChange={setSend} />}
          <Feedback problem={action.problem} />
          <div className="flex flex-wrap justify-end gap-2">
            <AccountButton name="cancel" onClick={onClose}>
              {t('common.cancel')}
            </AccountButton>
            <AccountButton name={linkSubmit(sends)} type="submit" testId="invite-submit" disabled={action.busy} />
          </div>
        </form>
      )}
    </Dialog>
  )
}
