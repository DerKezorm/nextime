/**
 * The operator's cards in the settings' group "Server" (drawing T1-Server, TIME-1): the switches, the limits, the
 * backups and the state of HTTPS and the internet, the invitation mail, the operator's own languages and the data
 * folder; sign-in is in `SignInCards.tsx`. `SettingsAreas.tsx` puts each card in its area. Everything here is the
 * server's; it applies to everyone.
 */
import { Activity, Download, Gauge, Globe, HardDrive, Mail, RotateCcw, SlidersHorizontal, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import {
  api,
  ApiError,
  backupsApi,
  passwordHeader,
  settingsApi,
  type AddressQuestion,
  type BackupBrief,
  type BackupEntry,
  type BackupSummary,
  type ExtraLocale,
  type ServerSettings,
  type ServerState,
  type SettingsChange,
} from '../../api/client'
import { byName, forgetAddedLanguages, templateFile } from '../../i18n'
import { bytes, dayTime } from '../../lib/format'
import { dateIn } from '../../lib/display'
import { clock, moment, type TimeOptions } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { useAuth } from '../../state/auth'
import { ASKED, Button, Card, Confirm, Feedback, Input, saveAsFile, Select, Toggle, useAction } from './ui'

type Server = ReturnType<typeof useServerSettings>

// eslint-disable-next-line react-refresh/only-export-components
export function useServerSettings() {
  const [settings, setSettings] = useState<ServerSettings | null>(null)
  const action = useAction()
  useEffect(() => {
    settingsApi.read().then(setSettings, () => undefined)
  }, [])
  /** The switch moves at once; the server's answer confirms it, a refusal puts it back. */
  const save = async (change: SettingsChange, done?: string) => {
    const before = settings
    if (settings) setSettings({ ...settings, ...change } as ServerSettings)
    const ok = await action.run(async () => setSettings(await settingsApi.save(change)), done)
    if (!ok) setSettings(before)
    return ok
  }
  /**
   * Saves the public address. authentik, set up by the button, knows the old one: the server then answers 409
   * `public_url_change` and saves nothing. That comes back as the question to put to the operator.
   */
  const saveAddress = async (public_url: string, confirm: boolean, done?: string): Promise<AddressQuestion | null> => {
    let question: AddressQuestion | null = null
    await action.run(async () => {
      try {
        setSettings(await settingsApi.save({ public_url, confirm_address: confirm }))
      } catch (error) {
        if (!(error instanceof ApiError) || error.code !== 'public_url_change') throw error
        question = { count: Number(error.values.count ?? 0), hand: Number(error.values.hand ?? 0), address: String(error.values.address ?? '') }
        return ASKED
      }
    }, done)
    return question
  }
  const reload = useCallback(() => settingsApi.read().then(setSettings, () => undefined), [])
  return { settings, save, saveAddress, reload, ...action }
}

/** Requests come with proxy headers, but no proxy is named: the sign-in brake sees one sender for everybody. */
export function ProxyHint({ server }: { server: Server }) {
  const { t } = useTranslation()
  if (!server.settings?.proxy_unknown) return null
  return (
    <p role="status" className="rounded-[12px] border border-warn-500/30 bg-warn-500/10 px-4 py-3 text-sm text-mist-100" data-testid="proxy-hint">
      {t('server.proxyUnknown')}
    </p>
  )
}

/** A whole number from the field, or what is wrong with it: empty, 0, negative, not a number (TIME-1 EC-11). */
// eslint-disable-next-line react-refresh/only-export-components
export function wholeNumber(text: string, lowest: number, highest: number): { value: number } | { problem: 'empty' | 'range' } {
  const trimmed = text.trim()
  if (!trimmed) return { problem: 'empty' }
  if (!/^\d+$/.test(trimmed)) return { problem: 'range' }
  const value = Number(trimmed)
  if (value < lowest || value > highest) return { problem: 'range' }
  return { value }
}

/** A field for a whole number with its unit, saved on its own; a wrong value is said at the field, the old one stays. */
function NumberField({
  id,
  label,
  unit,
  value,
  lowest,
  highest,
  onSave,
}: {
  id: string
  label: string
  unit: string
  value: number
  lowest: number
  highest: number
  onSave: (value: number) => Promise<boolean>
}) {
  const { t } = useTranslation()
  const [text, setText] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const submit = async () => {
    if (text === null) return
    const read = wholeNumber(text, lowest, highest)
    if ('problem' in read) {
      setProblem(read.problem === 'empty' ? t('server.numberEmpty') : t('server.numberRange', { lowest, highest }))
      return
    }
    setProblem(null)
    if (await onSave(read.value)) setText(null)
  }
  return (
    <form
      className="flex flex-wrap items-start gap-2"
      data-testid={id}
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <Input label={`${label} (${unit})`} inputMode="numeric" value={text ?? String(value)} onChange={setText} error={problem} className="w-44" />
      <span className="pt-5">
        <Button type="submit" disabled={text === null}>
          {t('common.save')}
        </Button>
      </span>
    </form>
  )
}

/** The limits (TIME-1 AC-4, EC-11, EC-12): the largest attachment, how long the trash keeps things; for new things. */
export function LimitsCard({ server }: { server: Server }) {
  const { t } = useTranslation()
  const s = server.settings
  if (!s) return null
  return (
    <Card id="limits" icon={Gauge} title={t('server.limits')}>
      <NumberField
        id="limit-upload"
        label={t('server.uploadMax')}
        unit={t('server.mb')}
        value={s.upload_max_mb}
        lowest={1}
        highest={10000}
        onSave={(upload_max_mb) => server.save({ upload_max_mb }, t('server.uploadSaved', { count: upload_max_mb }))}
      />
      <NumberField
        id="limit-trash"
        label={t('server.trash')}
        unit={t('server.daysUnit')}
        value={s.trash_days}
        lowest={1}
        highest={3650}
        onSave={(trash_days) => server.save({ trash_days }, t('server.trashSaved', { count: trash_days }))}
      />
      <Feedback problem={server.problem} done={server.done} />
    </Card>
  )
}

/** The switches of the features for every account (TIME-1 AC-5), titled, ordered and worded as nexnotes' card
 * "Features for every account": sharing by link, MCP access, the API; then nextime's own, Google and address
 * suggestions. Their effect comes with the features that use them; the setting is here from the start. Whether nextime
 * looks for new versions is switched under "About nextime", as in nexnotes. */
export function SwitchesCard({ server }: { server: Server }) {
  const { t } = useTranslation()
  const s = server.settings
  if (!s) return null
  return (
    <Card id="switches" icon={SlidersHorizontal} title={t('server.features')}>
      <div className="space-y-3" data-testid="switches">
        <Toggle label={t('server.shareLinks')} hint={t('server.shareLinksHint')} checked={s.share_links} onChange={(share_links) => void server.save({ share_links })} />
        <Toggle label={t('server.mcp')} hint={t('server.mcpHint')} checked={s.mcp_enabled} onChange={(mcp_enabled) => void server.save({ mcp_enabled })} />
        <Toggle label={t('server.api')} hint={t('server.apiHint')} checked={s.api_enabled} onChange={(api_enabled) => void server.save({ api_enabled })} />
        <Toggle label={t('server.localNetwork')} hint={t('server.localNetworkHint')} checked={s.connections_local_network} onChange={(connections_local_network) => void server.save({ connections_local_network })} />
        <Toggle
          label={t('server.google')}
          note={s.google_configured ? t('server.googleSet') : t('server.googleNotSet')}
          hint={t('server.googleHint')}
          checked={s.google_enabled}
          disabled={!s.google_configured}
          onChange={(google_enabled) => void server.save({ google_enabled })}
        />
        <Toggle
          label={t('server.addresses')}
          hint={t('server.addressesHint')}
          checked={s.address_suggestions}
          onChange={(address_suggestions) => void server.save({ address_suggestions })}
        />
      </div>
    </Card>
  )
}

/** "nextime is not reached over HTTPS …" (TIME-1 AC-6): for the operator, on the setup and on "Server". */
export function HttpsHint() {
  const { t } = useTranslation()
  return (
    <p role="status" className="rounded-[12px] border border-warn-500/30 bg-[var(--color-warn-wash)] px-4 py-3 text-sm text-warn-500" data-testid="https-hint">
      {t('server.httpsMissing')}
    </p>
  )
}

/** The box "State" (TIME-1 AC-9): HTTPS and the internet, green when right, a warning with what is missing; the log. */
export function StateCard({ state }: { state: ServerState | null }) {
  const { t } = useTranslation()
  const line = (ok: boolean, good: string, bad: string, testId: string) => (
    <p className={'flex items-start gap-2 text-sm ' + (ok ? 'text-mist-200' : 'text-warn-500')} data-testid={testId} data-ok={ok ? 'true' : 'false'}>
      <span aria-hidden className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: ok ? 'var(--color-ok-500)' : 'var(--color-warn-500)' }} />
      <span>{ok ? good : bad}</span>
    </p>
  )
  return (
    <Card id="state" icon={Activity} title={t('server.state')}>
      {state ? (
        <>
          {line(state.https, t('server.httpsOk'), t('server.httpsBad'), 'state-https')}
          {line(state.internet, t('server.internetOk'), t('server.internetBad'), 'state-internet')}
        </>
      ) : (
        <div className="h-10 animate-pulse rounded-[9px] bg-ink-800" aria-hidden />
      )}
      <Link to="/settings/log" className="nt-btn nt-btn-quiet text-sm">
        {t('server.showLog')}
      </Link>
    </Card>
  )
}

/** The data folder (what nextime takes and how full the disk is), on top of "Storage and backups" as in nexnotes. */
function StorageBlock({ server }: { server: Server }) {
  const { t, i18n } = useTranslation()
  const s = server.settings
  if (!s) return null
  const lang = i18n.language
  const own = s.nextime_used ?? 0
  const share = (part: number) => (s.data_total ? Math.min(100, (part / s.data_total) * 100) : 0)
  const ownShare = share(own)
  const otherShare = Math.min(100 - ownShare, share(Math.max(0, s.data_used - own)))
  const nearlyFull = s.data_total > 0 && s.data_used / s.data_total > 0.9
  const parts = STORAGE_PARTS.filter((part) => part === 'database' || (s.nextime_parts?.[part] ?? 0) > 0)
  return (
      <div className="space-y-1.5" data-testid="data-folder">
        <div className="flex flex-wrap justify-between gap-2 text-sm">
          <span className="text-mist-300">{t('server.nextimeUses')}</span>
          <span className="text-mist-100" data-testid="nextime-used">
            {bytes(own, lang)}
          </span>
        </div>
        <p className="text-xs text-mist-500" data-testid="nextime-parts">
          {parts.map((part) => t('server.part', { name: t(`server.parts.${part}`), size: bytes(s.nextime_parts?.[part] ?? 0, lang) })).join(' · ')}
        </p>
        <div className="flex h-2 overflow-hidden rounded-full bg-ink-700" role="img" aria-label={t('server.diskBar', { own: bytes(own, lang), used: bytes(s.data_used, lang), total: bytes(s.data_total, lang) })}>
          <div className="h-full bg-accent-500" style={{ width: `${ownShare}%`, minWidth: own > 0 ? 3 : 0 }} />
          <div className="h-full" style={{ width: `${otherShare}%`, background: nearlyFull ? 'var(--color-warn-500)' : 'var(--color-mist-500)', opacity: nearlyFull ? 1 : 0.35 }} />
        </div>
        <p className={'text-xs ' + (nearlyFull ? 'text-warn-500' : 'text-mist-500')} data-testid="disk-free">
          {t('server.diskFree', { free: bytes(s.data_free, lang), total: bytes(s.data_total, lang) })}
        </p>
        {s.data_on_share && <p className="rounded-[9px] border border-warn-500/30 bg-[var(--color-warn-wash)] px-3 py-2 text-xs text-warn-500">{t('setup.share')}</p>}
      </div>
  )
}

/** The parts of what nextime takes in its data folder, in the order the page names them. */
const STORAGE_PARTS = ['database', 'attachments', 'backups', 'other'] as const

/** "Daily 03:00 (Europe/Berlin) · last today 03:00, 14 MB · 14 kept" (TIME-1 AC-8): the rhythm in the server's time and
 * zone, which it names; the last backup in the account's clock and zone (TIME-3 AC-6, AC-8). */
// eslint-disable-next-line react-refresh/only-export-components
export function backupLine(
  summary: BackupSummary,
  t: (key: string, values?: Record<string, unknown>) => string,
  lang: string,
  now = new Date(),
  options: TimeOptions = {},
): string {
  const rhythm = summary.schedule === 'daily' ? t('server.backupDaily', { time: summary.time, zone: summary.zone }) : t('server.backupOff')
  const parts = [rhythm]
  if (summary.last) {
    const made = new Date(summary.last.created)
    const zone = options.zone || Intl.DateTimeFormat().resolvedOptions().timeZone
    const sameDay = JSON.stringify(dateIn(made, zone)) === JSON.stringify(dateIn(now, zone))
    const when = sameDay ? t('server.todayAt', { time: clock(made, lang, options) }) : moment(made, lang, false, options)
    parts.push(t('server.backupLast', { when, size: bytes(summary.last.size, lang) }))
  } else parts.push(t('server.noBackups'))
  parts.push(t('server.backupKept', { count: summary.kept }))
  return parts.join(' · ')
}

/** The box "Storage and backups", as nexnotes' (TIME-1 AC-2, AC-8, EC-7, EC-15): the data folder on top, then the
 * rhythm, the last backup, how many are kept; "Back up now" (one at a time, the button shows it running) and
 * "Restore …", which checks first and says what comes, changes and goes. */
export function BackupsCard({ server }: { server: Server }) {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const [summary, setSummary] = useState<BackupSummary | null>(null)
  const [list, setList] = useState<BackupEntry[]>([])
  const [listOpen, setListOpen] = useState(false)
  const [brief, setBrief] = useState<BackupBrief | null>(null)
  const [asking, setAsking] = useState<{ kind: 'restore' | 'download' | 'delete' | 'upload'; name: string; file?: File; brief?: BackupBrief } | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const [restarting, setRestarting] = useState(false)
  const [uploaded, setUploaded] = useState(false)
  const [running, setRunning] = useState(false)
  const action = useAction()
  const { timeOptions } = useDisplay()
  const load = useCallback(() => {
    backupsApi.summary().then(setSummary, () => undefined)
    backupsApi.list().then(setList, () => undefined)
  }, [])
  useEffect(load, [load])
  const refresh = () => {
    load()
    void server.reload()
  }
  const s = server.settings
  if (!s) return null
  const lang = i18n.language
  const forBackup = (entry: BackupEntry, verb: string) => t('server.forBackup', { name: moment(entry.created, lang, false, timeOptions), action: verb })
  const changes = (b: BackupBrief) =>
    t('server.restoreChanges', { add: t('server.countFiles', { count: b.would_add }), change: t('server.countFiles', { count: b.would_change }), remove: t('server.countFiles', { count: b.would_remove }) })
  const eventChanges = (b: BackupBrief) => (
    <div className="space-y-1 text-sm" data-testid="restore-events">
      <p>{t('server.restoreEvents', { add: b.events_add, change: b.events_change, remove: b.events_remove })}</p>
      <p className="text-xs text-mist-400">{t('server.restoreAccounts', { add: b.accounts_add, remove: b.accounts_remove })}</p>
    </div>
  )
  const busyNow = running || Boolean(summary?.running)
  return (
    <Card id="backups" icon={HardDrive} title={t('server.storage')} text={t('server.backupHolds')}>
      <StorageBlock server={server} />
      <p className="text-sm text-mist-300" data-testid="backup-line">
        {summary ? backupLine(summary, t, lang, new Date(), timeOptions) : '…'}
      </p>
      {summary?.failure && (
        <p role="alert" className="rounded-[9px] border border-bad-500/40 px-3 py-2 text-sm text-bad-500" data-testid="backup-failure">
          {t('server.backupFailed', { when: moment(summary.failure.at, lang, false, timeOptions), reason: t(`server.backupReasons.${summary.failure.reason === 'disk_full' ? 'disk_full' : 'write_failed'}`) })}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          accent
          busy={busyNow}
          onClick={() => {
            setRunning(true)
            void action
              .run(async () => {
                await backupsApi.make()
                refresh()
              }, t('server.backupMade'))
              .finally(() => setRunning(false))
          }}
        >
          {busyNow ? t('server.backupRunning') : t('server.backupNow')}
        </Button>
        <Button onClick={() => setListOpen((open) => !open)}>{t('server.restoreOpen')}</Button>
      </div>
      <Feedback problem={action.problem} done={action.done} />
      <details className="text-sm">
        <summary className="cursor-pointer text-xs text-mist-400">{t('server.backupPlan')}</summary>
        <div className="mt-3 flex flex-wrap items-start gap-3">
          <Select
            label={t('server.schedule')}
            value={s.backup_schedule}
            onChange={(backup_schedule) => void server.save({ backup_schedule }).then(load)}
            options={[
              { value: 'off', label: t('server.off') },
              { value: 'daily', label: t('server.daily') },
            ]}
            className="w-36"
          />
          <BackupTime value={s.backup_time} onSave={(backup_time) => server.save({ backup_time }, t('settings.saved')).then((ok) => (load(), ok))} />
          <NumberField
            id="backup-keep"
            label={t('server.keep')}
            unit={t('server.pieces')}
            value={s.backup_keep}
            lowest={1}
            highest={365}
            onSave={(backup_keep) => server.save({ backup_keep }, t('settings.saved')).then((ok) => (load(), ok))}
          />
        </div>
      </details>
      {listOpen && (
        <div className="space-y-3" data-testid="restore-panel">
          <p className="text-xs text-mist-500">{t('server.restoreLead')}</p>
          <input
            ref={picker}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (file) setAsking({ kind: 'upload', name: file.name, file })
            }}
          />
          <Button small onClick={() => picker.current?.click()}>
            <Upload className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden />
            {t('server.uploadBackup')}
          </Button>
          {list.length > 0 && (
            <ul className="divide-y divide-ink-700 rounded-[12px] border border-ink-700 text-sm" data-testid="backup-list">
              {list.map((entry) => (
                <li key={entry.name} className="flex flex-wrap items-center gap-2 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="text-mist-100">{dayTime(entry.created, lang, timeOptions)}</div>
                    <div className="text-xs text-mist-500">
                      {entry.uploaded ? t('server.uploaded') : t(`server.kinds.${entry.kind}`)} · {bytes(entry.size, lang)} · {entry.version}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Button
                      small
                      label={forBackup(entry, t('server.restore'))}
                      onClick={() =>
                        void action.run(async () => {
                          // First the trial run: what comes, what changes, what goes; then the question.
                          const checked = await backupsApi.check(entry.name)
                          setBrief(checked)
                          if (checked.usable) setAsking({ kind: 'restore', name: entry.name, brief: checked })
                        })
                      }
                    >
                      <RotateCcw className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden />
                      {t('server.restore')}
                    </Button>
                    <Button small label={forBackup(entry, t('server.download'))} onClick={() => setAsking({ kind: 'download', name: entry.name })}>
                      <Download className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden />
                      {t('server.download')}
                    </Button>
                    <Button small danger label={forBackup(entry, t('server.deleteBackup'))} onClick={() => setAsking({ kind: 'delete', name: entry.name })}>
                      <Trash2 className="h-3.5 w-3.5" strokeWidth={1.8} aria-hidden />
                      {t('server.deleteBackup')}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {brief && !brief.usable && (
        <p data-testid="backup-brief" className="rounded-[12px] border border-bad-500/40 px-4 py-3 text-sm text-bad-500">
          {t('server.checkBad')}
        </p>
      )}
      {uploaded && <p className="rounded-[12px] border border-ok-500/40 bg-[var(--color-ok-wash)] px-4 py-3 text-sm text-mist-200">{t('server.uploadedHint')}</p>}
      {restarting && <p className="rounded-[12px] border border-warn-500/40 bg-[var(--color-warn-wash)] px-4 py-3 text-sm text-warn-500">{t('server.restarting')}</p>}
      {asking && (
        <Confirm
          title={t(`server.confirm.backup_${asking.kind}.title`)}
          text={asking.kind === 'restore' && asking.brief ? `${changes(asking.brief)} ${t('server.confirm.backup_restore.text')}` : t(`server.confirm.backup_${asking.kind}.text`)}
          details={asking.kind === 'restore' && asking.brief ? eventChanges(asking.brief) : undefined}
          confirm={t(`server.confirm.backup_${asking.kind}.button`)}
          danger={asking.kind !== 'download'}
          password={me?.sign_in === 'password'}
          onCancel={() => setAsking(null)}
          onConfirm={async (password) => {
            if (asking.kind === 'download') saveAsFile(asking.name, await backupsApi.download(asking.name, password))
            if (asking.kind === 'delete') {
              await backupsApi.remove(asking.name, password)
              refresh()
            }
            if (asking.kind === 'upload' && asking.file) {
              await api('/api/backups/upload', { method: 'POST', raw: asking.file, headers: { 'X-Nextime-Password': passwordHeader(password) } })
              refresh()
              setUploaded(true)
            }
            if (asking.kind === 'restore') {
              await backupsApi.restore(asking.name, password)
              setRestarting(true)
              window.setTimeout(() => window.location.reload(), 8000)
            }
            setAsking(null)
          }}
        />
      )}
    </Card>
  )
}

/** The time of the daily backup, in the server's zone; a time that is none is said at the field. */
function BackupTime({ value, onSave }: { value: string; onSave: (value: string) => Promise<boolean> }) {
  const { t } = useTranslation()
  const [text, setText] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  return (
    <form
      className="flex items-start gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        if (text === null) return
        if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(text.trim())) {
          setProblem(t('server.timeInvalid'))
          return
        }
        setProblem(null)
        void onSave(text.trim()).then((ok) => ok && setText(null))
      }}
    >
      <Input label={t('server.backupTime')} value={text ?? value} onChange={setText} error={problem} className="w-28" />
      <span className="pt-5">
        <Button type="submit" disabled={text === null}>
          {t('common.save')}
        </Button>
      </span>
    </form>
  )
}

export function MailCard({ server }: { server: Server }) {
  const { t, i18n } = useTranslation()
  const [draft, setDraft] = useState<SettingsChange>({})
  const [to, setTo] = useState('')
  const test = useAction()
  const s = server.settings
  if (!s) return null
  const value = { ...s, ...draft }
  return (
    <Card id="mail" icon={Mail} title={t('server.mail')} text={t('server.mailHint')}>
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault()
          void server.save(draft, t('settings.saved')).then(() => setDraft({}))
        }}
      >
        <Input label={t('server.smtpHost')} value={value.smtp_host ?? ''} onChange={(smtp_host) => setDraft({ ...draft, smtp_host })} placeholder="smtp.example.com" />
        <Input label={t('server.smtpPort')} type="number" value={String(value.smtp_port)} onChange={(port) => setDraft({ ...draft, smtp_port: Number(port) })} />
        <Select
          label={t('server.smtpSecurity')}
          value={value.smtp_security ?? 'starttls'}
          onChange={(smtp_security) => setDraft({ ...draft, smtp_security })}
          options={[
            { value: 'starttls', label: 'STARTTLS' },
            { value: 'tls', label: 'TLS' },
            { value: 'none', label: t('server.none') },
          ]}
        />
        <Input label={t('server.smtpUser')} value={value.smtp_user ?? ''} onChange={(smtp_user) => setDraft({ ...draft, smtp_user })} />
        <Input label={t('auth.password')} type="password" autoComplete="new-password" value={draft.smtp_password ?? ''} onChange={(smtp_password) => setDraft({ ...draft, smtp_password })} placeholder={s.smtp_password_set ? t('server.secretKept') : ''} />
        <Input label={t('server.smtpFrom')} value={value.smtp_from ?? ''} onChange={(smtp_from) => setDraft({ ...draft, smtp_from })} placeholder="time@example.com" />
        <div className="sm:col-span-2">
          <Button type="submit" accent busy={server.busy}>
            {t('common.save')}
          </Button>
        </div>
      </form>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          void test.run(() => settingsApi.mailTest(to, i18n.language), t('server.mailSent'))
        }}
      >
        <Input label={t('server.mailTest')} value={to} onChange={setTo} placeholder="you@example.com" className="min-w-60 flex-1" />
        <Button type="submit" busy={test.busy} disabled={!to.trim()}>
          {t('server.send')}
        </Button>
      </form>
      <Feedback problem={test.problem ?? server.problem} done={test.done ?? server.done} />
    </Card>
  )
}

/** The operator's own languages (TIME-3 AC-1, EC-3): files in the data folder, or uploaded here. */
export function LanguagesCard() {
  const { t } = useTranslation()
  const [list, setList] = useState<ExtraLocale[]>([])
  const [code, setCode] = useState('')
  const file = useRef<HTMLInputElement>(null)
  const action = useAction()
  const load = useCallback(() => {
    api<ExtraLocale[]>('/api/locales').then((rows) => setList(rows.sort(byName)), () => undefined)
  }, [])
  useEffect(load, [load])
  const chip = 'flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs'
  return (
    <Card id="languages" icon={Globe} title={t('server.languages')} text={t('server.languagesHint')}>
      <div className="flex flex-wrap gap-2">
        <span className={chip + ' border-ink-700 text-mist-300'}>Deutsch · {t('server.builtIn')}</span>
        <span className={chip + ' border-ink-700 text-mist-300'}>English · {t('server.builtIn')}</span>
        {list.map((entry) => (
          <span key={entry.code} className={chip + ' border-accent-700/40 text-mist-200'}>
            {entry.name} ({entry.code}) · {t('server.texts', { count: entry.keys })}
            <button
              type="button"
              aria-label={t('server.remove')}
              className="text-mist-500 hover:text-bad-500"
              onClick={() =>
                void action.run(async () => {
                  await api(`/api/locales/${entry.code}`, { method: 'DELETE' })
                  forgetAddedLanguages()
                  load()
                })
              }
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Button onClick={() => saveAsFile('nextime-language-template.json', templateFile())}>
          <Download className="h-4 w-4" strokeWidth={1.8} />
          {t('server.template')}
        </Button>
        <Input label={t('server.languageCode')} value={code} onChange={(value) => setCode(value.trim())} placeholder="nl" className="w-28" />
        <Button disabled={!code} busy={action.busy} onClick={() => file.current?.click()}>
          <Upload className="h-4 w-4" strokeWidth={1.8} />
          {t('server.upload')}
        </Button>
        <input
          ref={file}
          type="file"
          accept="application/json,.json"
          hidden
          onChange={(e) => {
            const chosen = e.target.files?.[0]
            e.target.value = ''
            if (!chosen) return
            void action.run(async () => {
              await api(`/api/locales/${encodeURIComponent(code)}`, { method: 'PUT', raw: chosen })
              forgetAddedLanguages()
              load()
            }, t('server.languageAdded'))
          }}
        />
      </div>
      <Feedback problem={action.problem} done={action.done} />
    </Card>
  )
}

