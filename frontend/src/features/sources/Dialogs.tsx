/**
 * The dialogs behind "Add calendar" and "Calendars and accounts": connecting iCloud with its guide and another CalDAV
 * server (TIME-15 AC-1, AC-2, AC-8, EC-4, EC-13, EC-14, EC-17), choosing the calendars of an account (AC-9, AC-14,
 * EC-10, EC-11), connecting again (AC-11), "Connect with Google" and Google's read-only way (TIME-16 AC-2, AC-4,
 * AC-9), a subscription with its preview (TIME-17 AC-1, AC-2, EC-2, EC-3, EC-6), holidays and school holidays (AC-3,
 * AC-4, AC-8, EC-13), a new address for a subscription (TIME-16 EC-11) and disconnecting (TIME-15 AC-5, EC-8).
 *
 * Every dialog sends once, however often it is clicked (EC-15, TIME-17 EC-9); what a source sends is shown as text.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { CheckLine } from '../../components/CheckLine'
import { errorText } from '../../lib/errors'
import { browser, newRef } from '../calendars/api'
import { useCalendars } from '../calendars/store'
import { Swatch } from '../calendars/Swatch'
import { sourcesApi, type Choices, type HolidayCountry, type Login, type Preview, type SourceRow, type Sources } from './api'
import { useKindName } from './store'

/** The field an error belongs to, from the server's answer. */
function fieldOf(error: unknown): string {
  return error instanceof ApiError ? String(error.values.field ?? '') : ''
}

function textOf(error: unknown): string {
  return error instanceof ApiError ? errorText(error.code, error.values) : errorText('network')
}

function TextField({ label, value, onChange, type = 'text', problem, placeholder, testId, onBlur, autoComplete = 'off' }: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  problem?: string | null
  placeholder?: string
  testId: string
  onBlur?: () => void
  autoComplete?: string
}) {
  const id = useId()
  return (
    <div className="text-sm">
      <label htmlFor={id} className="font-medium text-mist-100">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        autoComplete={autoComplete}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        aria-invalid={problem ? true : undefined}
        aria-describedby={problem ? `${id}-problem` : undefined}
        className={'nt-field mt-1 ' + (problem ? 'border-bad-500' : '')}
        data-testid={testId}
      />
      {problem && (
        <span id={`${id}-problem`} role="alert" className="mt-1 block text-xs text-bad-500" data-testid={`${testId}-problem`}>
          {problem}
        </span>
      )}
    </div>
  )
}

function Switch({ label, checked, onChange, testId }: { label: string; checked: boolean; onChange: (value: boolean) => void; testId: string }) {
  return <CheckLine label={label} checked={checked} onChange={onChange} testId={testId} />
}

function Trouble({ text }: { text: string | null }) {
  if (!text) return null
  return (
    <p role="alert" className="rounded-[9px] border border-bad-500/30 bg-bad-500/10 px-3 py-2 text-sm text-bad-500" data-testid="dialog-problem">
      {text}
    </p>
  )
}

function Buttons({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-end gap-2 pt-1">{children}</div>
}

/** One request at a time from a dialog: a second click while the first runs does nothing (EC-15). */
function useOnce(): [boolean, (work: () => Promise<void>) => Promise<void>] {
  const running = useRef(false)
  const [busy, setBusy] = useState(false)
  const run = async (work: () => Promise<void>) => {
    if (running.current) return
    running.current = true
    setBusy(true)
    try {
      await work()
    } finally {
      running.current = false
      setBusy(false)
    }
  }
  return [busy, run]
}

// ---- iCloud and CalDAV -----------------------------------------------------------------------------------------------

/** "Connect iCloud" with the guide to the app-specific password, or "Other CalDAV server" (TIME-15 AC-1, AC-2, AC-8). */
export function ConnectDialog({ kind, onConnected, onExists, onClose }: {
  kind: 'icloud' | 'caldav'
  onConnected: (choices: Choices) => void
  /** The same account is connected already: go to choosing its calendars (EC-14). */
  onExists: (sourceId: number) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [server, setServer] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [problems, setProblems] = useState<Record<string, string>>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()
  const ref = useRef(newRef())

  const connect = () =>
    run(async () => {
      setProblems({})
      setProblem(null)
      // Spaces at the edges from the clipboard go (EC-13).
      const clean = { server: server.trim(), username: username.trim(), password: password.trim() }
      try {
        onConnected(await sourcesApi.connect(kind, clean.server, clean.username, clean.password, ref.current))
      } catch (error) {
        if (error instanceof ApiError && error.code === 'source_exists' && error.values.source_id) return onExists(Number(error.values.source_id))
        const field = fieldOf(error)
        if (['server', 'username', 'password'].includes(field)) setProblems({ [field]: textOf(error) })
        else setProblem(textOf(error))
      }
    })

  return (
    <Dialog title={t(`sources.connect.${kind}.title`)} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void connect()
        }}
        data-testid="connect-dialog"
      >
        {kind === 'caldav' && (
          <TextField label={t('sources.connect.caldav.server')} value={server} onChange={setServer} placeholder="https://cloud.example.net" problem={problems.server} testId="connect-server" />
        )}
        <TextField label={t(`sources.connect.${kind}.username`)} value={username} onChange={setUsername} problem={problems.username} testId="connect-username" />
        <TextField label={t(`sources.connect.${kind}.password`)} type="password" value={password} onChange={setPassword} problem={problems.password} testId="connect-password" />
        {kind === 'icloud' && <AppleGuide />}
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={busy} data-testid="connect-go">
            {busy ? t('sources.checking') : t('sources.connect.go')}
          </button>
        </Buttons>
      </form>
    </Dialog>
  )
}

/** How to get an app-specific password, in three steps, with Apple's menus in the app's language (AC-8, EC-24). */
function AppleGuide() {
  const { t } = useTranslation()
  return (
    <div className="space-y-3" data-testid="apple-guide">
      <div className="space-y-1 rounded-[12px] border border-ink-700 bg-ink-900 px-3.5 py-3 text-[13px] text-mist-200">
        <strong className="block text-mist-100">{t('sources.apple.title')}</strong>
        <ol className="list-inside list-decimal space-y-0.5" data-testid="apple-steps">
          <li>{t('sources.apple.step1')}</li>
          <li>{t('sources.apple.step2')}</li>
          <li>{t('sources.apple.step3')}</li>
        </ol>
        <a href="https://account.apple.com" target="_blank" rel="noopener noreferrer" className="nt-btn nt-btn-quiet text-sm" data-testid="apple-page">
          {t('sources.apple.open')}
        </a>
      </div>
      <p className="text-xs text-mist-500">{t('sources.apple.safe')}</p>
    </div>
  )
}

/** A new password for an access that expired (AC-11): calendars, colors, collections and shares stay as they are. */
export function ReconnectDialog({ row, onDone, onClose }: { row: SourceRow; onDone: (sources: Sources) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [password, setPassword] = useState('')
  const [fieldProblem, setFieldProblem] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()
  const kind = row.kind === 'icloud' ? 'icloud' : 'caldav'
  const send = () =>
    run(async () => {
      setFieldProblem(null)
      setProblem(null)
      try {
        onDone(await sourcesApi.reconnect(row.id, password.trim()))
      } catch (error) {
        if (fieldOf(error) === 'password') setFieldProblem(textOf(error))
        else setProblem(textOf(error))
      }
    })
  return (
    <Dialog title={t('sources.reconnectTitle', { name: row.label })} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void send()
        }}
        data-testid="reconnect-dialog"
      >
        <p className="text-sm text-mist-200">{t('sources.reconnectText')}</p>
        <TextField label={t(`sources.connect.${kind}.password`)} type="password" value={password} onChange={setPassword} problem={fieldProblem} testId="reconnect-password" />
        {kind === 'icloud' && <AppleGuide />}
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={busy} data-testid="reconnect-go">
            {busy ? t('sources.checking') : t('sources.connect.go')}
          </button>
        </Buttons>
      </form>
    </Dialog>
  )
}

// ---- Choosing the calendars of an account --------------------------------------------------------------------------------

/**
 * "iCloud verbunden – Welche Kalender sollen in nextime erscheinen?" (AC-9): every calendar of the account with its
 * color and a tick, "nur lesen" and "neu" (AC-6, AC-14); without calendars a sentence saying so (EC-10). Over 20
 * calendars goes, with the hint of TIME-4 AC-6 (EC-11).
 */
export function ChooseDialog({ choices, connected, note, onDone, onClose }: {
  choices: Choices
  /** Just connected: "iCloud verbunden"; else "iCloud-Kalender wählen". */
  connected: boolean
  note?: string | null
  onDone: (sources: Sources) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { list } = useCalendars()
  const kindName = useKindName()
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(choices.calendars.filter((entry) => entry.chosen).map((entry) => entry.url)))
  const [problem, setProblem] = useState<string | null>(null)
  // Calendars left out still have changes waiting: how many, said before they get lost (TIME-15 EC-8).
  const [waiting, setWaiting] = useState(0)
  const [busy, run] = useOnce()
  // All calendars of the account from all sources (TIME-4 AC-6), with this account's as ticked now.
  const limit = list?.smooth_up_to ?? 20
  const before = list?.calendars.filter((entry) => entry.origin?.id === choices.source_id).length ?? 0
  const after = (list?.count ?? 0) - before + chosen.size
  const kind = kindName(choices.kind)
  const done = () =>
    run(async () => {
      setProblem(null)
      try {
        const urls = choices.calendars.filter((entry) => chosen.has(entry.url)).map((entry) => entry.url)
        onDone(await sourcesApi.choose(choices.source_id, urls, waiting > 0))
      } catch (error) {
        if (error instanceof ApiError && error.code === 'changes_waiting') return setWaiting(Number(error.values.count ?? 1))
        setProblem(textOf(error))
      }
    })
  const flip = (url: string) =>
    setChosen((value) => {
      const next = new Set(value)
      if (next.has(url)) next.delete(url)
      else next.add(url)
      return next
    })
  return (
    <Dialog title={connected ? t('sources.connectedTitle', { kind }) : t('sources.chooseTitle', { kind })} onClose={onClose}>
      <div className="space-y-4" data-testid="choose-dialog">
        {note && (
          <p role="status" className="rounded-[9px] border border-ink-700 bg-[var(--color-accent-wash)] px-3 py-2 text-sm text-mist-200" data-testid="choose-note">
            {note}
          </p>
        )}
        <p className="text-sm text-mist-400">{t('sources.chooseText')}</p>
        {choices.calendars.length === 0 ? (
          <p className="nt-empty py-6" data-testid="choose-none">
            {t('sources.chooseNone')}
          </p>
        ) : (
          <ul className="divide-y divide-line overflow-hidden rounded-[12px] border border-ink-700" data-testid="choose-list">
            {choices.calendars.map((entry) => (
              <li key={entry.url}>
                <label className="flex min-w-0 cursor-pointer items-center gap-3 px-3 py-2.5 text-sm">
                  <input type="checkbox" checked={chosen.has(entry.url)} onChange={() => flip(entry.url)} className="h-4 w-4 shrink-0 accent-[var(--color-accent-500)]" data-testid="choose-box" />
                  <Swatch color={entry.color} />
                  <span className="min-w-0 flex-1 truncate text-mist-100" title={entry.name} data-testid="choose-name">
                    {entry.name}
                  </span>
                  {entry.new && <span className="nt-mark shrink-0" data-testid="mark-new">{t('sources.markNew')}</span>}
                  {entry.read_only && <span className="nt-mark nt-mark-gray shrink-0" data-testid="mark-read-only">{t('calendars.tags.readOnly')}</span>}
                </label>
              </li>
            ))}
          </ul>
        )}
        {after > limit && (
          <p className="text-xs text-warn-500" data-testid="choose-over">
            {t('calendars.countOver', { count: after })}
          </p>
        )}
        {waiting > 0 && (
          <p role="alert" className="rounded-[9px] border border-warn-500/30 bg-warn-wash px-3 py-2 text-sm text-warn-500" data-testid="choose-waiting">
            {t('sources.waitingLostChoose', { count: waiting, kind })}
          </p>
        )}
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-accent" disabled={busy} onClick={() => void done()} data-testid="choose-done">
            {t('common.done')}
          </button>
        </Buttons>
      </div>
    </Dialog>
  )
}

// ---- Google ------------------------------------------------------------------------------------------------------------

/** "Connect with Google" (TIME-16 AC-2): what Google will show, then off to Google; also "Connect again" (AC-8). */
export function GoogleDialog({ sourceId, onClose }: { sourceId?: number; onClose: () => void }) {
  const { t } = useTranslation()
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()
  const go = () =>
    run(async () => {
      setProblem(null)
      try {
        const { url } = await sourcesApi.googleStart(sourceId)
        browser.go(url)
      } catch (error) {
        setProblem(textOf(error))
      }
    })
  return (
    <Dialog title={t('sources.google.title')} onClose={onClose}>
      <div className="space-y-4" data-testid="google-dialog">
        <p className="rounded-[12px] border border-ink-700 bg-ink-900 px-3.5 py-3 text-[13.5px] leading-relaxed text-mist-200" data-testid="google-unverified">
          {t('sources.google.unverified')}
        </p>
        <p className="text-sm text-mist-400">{t('sources.google.only')}</p>
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="nt-btn nt-btn-accent" disabled={busy} onClick={() => void go()} data-testid="google-go">
            {t('sources.google.go')}
          </button>
        </Buttons>
      </div>
    </Dialog>
  )
}

/** "Google-Kalender nur lesen" while Google is not set up (TIME-16 AC-4, AC-9, EC-10). */
export function GoogleReadDialog({ onDone, onClose }: { onDone: (sources: Sources) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [url, setUrl] = useState('')
  const [fieldProblem, setFieldProblem] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()
  const ref = useRef(newRef())
  const add = () =>
    run(async () => {
      setFieldProblem(null)
      setProblem(null)
      if (!url.trim()) return setFieldProblem(t('sources.google.addressMissing'))
      try {
        onDone(await sourcesApi.subscribe(url.trim(), '', false, {}, true, ref.current))
      } catch (error) {
        if (error instanceof ApiError && error.status !== 502 && fieldOf(error) !== 'password') setFieldProblem(textOf(error))
        else setProblem(textOf(error))
      }
    })
  return (
    <Dialog title={t('sources.googleRead.title')} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void add()
        }}
        data-testid="google-read-dialog"
      >
        <p className="text-xs font-semibold tracking-wider text-mist-500 uppercase">{t('sources.googleRead.notSetUp')}</p>
        <p className="text-sm text-mist-200">{t('sources.googleRead.text')}</p>
        <ol className="list-inside list-decimal space-y-0.5 text-[13px] text-mist-200" data-testid="google-read-steps">
          <li>{t('sources.googleRead.step1')}</li>
          <li>{t('sources.googleRead.step2')}</li>
        </ol>
        <TextField label={t('sources.googleRead.address')} value={url} onChange={setUrl} placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" problem={fieldProblem} testId="google-read-url" />
        <p className="text-xs text-mist-500" data-testid="hourly">
          {t('sources.hourly')}
        </p>
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={busy} data-testid="google-read-go">
            {t('sources.googleRead.add')}
          </button>
        </Buttons>
      </form>
    </Dialog>
  )
}

// ---- Subscriptions -----------------------------------------------------------------------------------------------------

/** "Januar bis Dezember 2027", "Oktober 2026 bis März 2027", "Mai 2027". */
function span(first: string | null, last: string | null, language: string, t: (key: string, values?: Record<string, unknown>) => string): string {
  if (!first || !last) return ''
  const month = (day: string, year: boolean) => new Intl.DateTimeFormat(language, { month: 'long', ...(year ? { year: 'numeric' } : {}), timeZone: 'UTC' }).format(new Date(`${day.slice(0, 10)}T12:00:00Z`))
  const sameYear = first.slice(0, 4) === last.slice(0, 4)
  if (first.slice(0, 7) === last.slice(0, 7)) return month(first, true)
  return t('sources.subscription.span', { from: month(first, !sameYear), to: month(last, true) })
}

const SUBSCRIPTION_FIELD = new Set(['subscription_not_a_calendar', 'subscription_gone', 'subscription_too_large', 'subscription_exists', 'invalid_url', 'not_google_address', 'subscription_unreachable'])

/** "Kalender abonnieren" (TIME-17 AC-1): the preview after the link, "Name in nextime", "Als Hintergrund zeigen". */
export function SubscribeDialog({ onDone, onClose }: { onDone: (sources: Sources) => void; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [named, setNamed] = useState(false)
  const [background, setBackground] = useState(true)
  const [login, setLogin] = useState<Login | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [looked, setLooked] = useState('')
  const [urlProblem, setUrlProblem] = useState<string | null>(null)
  const [passwordProblem, setPasswordProblem] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()
  const ref = useRef(newRef())

  const refused = (error: unknown) => {
    if (error instanceof ApiError && error.code === 'subscription_login_needed') {
      setLogin((value) => value ?? { username: '', password: '' })
      setProblem(errorText(error.code))
      return
    }
    if (fieldOf(error) === 'password' || (error instanceof ApiError && error.code === 'subscription_login_refused')) return setPasswordProblem(textOf(error))
    if (error instanceof ApiError && SUBSCRIPTION_FIELD.has(error.code)) return setUrlProblem(textOf(error))
    setProblem(textOf(error))
  }

  const look = async () => {
    const clean = url.trim()
    if (!clean) return
    const key = `${clean}|${login?.username ?? ''}|${login?.password ?? ''}`
    if (key === looked) return
    setLooked(key)
    setUrlProblem(null)
    setPasswordProblem(null)
    setProblem(null)
    try {
      const found = await sourcesApi.preview(clean, login ?? {})
      setPreview(found)
      if (!named) setName(found.name)
    } catch (error) {
      setPreview(null)
      refused(error)
    }
  }

  const subscribe = () =>
    run(async () => {
      setUrlProblem(null)
      setPasswordProblem(null)
      setProblem(null)
      if (!url.trim()) return setUrlProblem(t('sources.subscription.linkMissing'))
      try {
        onDone(await sourcesApi.subscribe(url.trim(), name.trim(), background, login ?? {}, false, ref.current))
      } catch (error) {
        refused(error)
      }
    })

  const range = preview ? span(preview.first, preview.last, i18n.language, t) : ''
  return (
    <Dialog title={t('sources.subscription.title')} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void subscribe()
        }}
        data-testid="subscribe-dialog"
      >
        <TextField
          label={t('sources.subscription.link')}
          value={url}
          onChange={(value) => {
            setUrl(value)
            setPreview(null)
          }}
          onBlur={() => void look()}
          placeholder="https://… .ics"
          problem={urlProblem}
          testId="subscribe-url"
        />
        {login && (
          <div className="grid gap-3 sm:grid-cols-2" data-testid="subscribe-login">
            <TextField label={t('sources.subscription.username')} value={login.username ?? ''} onChange={(value) => setLogin({ ...login, username: value })} testId="subscribe-username" />
            <TextField
              label={t('sources.subscription.password')}
              type="password"
              value={login.password ?? ''}
              onChange={(value) => setLogin({ ...login, password: value })}
              onBlur={() => void look()}
              problem={passwordProblem}
              testId="subscribe-password"
            />
          </div>
        )}
        {preview && (
          <div className="space-y-1 rounded-[12px] border border-ink-700 bg-ink-900 px-3.5 py-3" data-testid="subscribe-preview">
            <strong className="block truncate text-sm text-mist-100" title={preview.name}>
              {preview.name}
            </strong>
            <span className="block text-[13px] text-mist-400" data-testid="subscribe-summary">
              {[t('sources.subscription.events', { count: preview.count }), range, t('calendars.tags.readOnly')].filter(Boolean).join(' · ')}
            </span>
            {preview.over && (
              <span className="block text-xs text-warn-500" data-testid="subscribe-over">
                {t('sources.subscription.over', { total: (preview.events_total + preview.count).toLocaleString(i18n.language), limit: preview.events_smooth_up_to.toLocaleString(i18n.language) })}
              </span>
            )}
            {preview.exists !== null && (
              <span className="block text-xs text-warn-500" data-testid="subscribe-exists">
                {errorText('subscription_exists')}
              </span>
            )}
          </div>
        )}
        <TextField
          label={t('sources.subscription.name')}
          value={name}
          onChange={(value) => {
            setName(value)
            setNamed(true)
          }}
          testId="subscribe-name"
        />
        <Switch label={t('sources.subscription.background')} checked={background} onChange={setBackground} testId="subscribe-background" />
        <p className="text-xs text-mist-500" data-testid="hourly">
          {t('sources.hourly')}
        </p>
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={busy || (preview !== null && preview.exists !== null)} data-testid="subscribe-go">
            {t('sources.subscription.go')}
          </button>
        </Buttons>
      </form>
    </Dialog>
  )
}

/** A new link for a subscription whose old one stopped working, as Google's after resetting it (TIME-16 EC-11). */
export function LinkDialog({ row, onDone, onClose }: { row: SourceRow; onDone: (sources: Sources) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [url, setUrl] = useState('')
  const [fieldProblem, setFieldProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()
  const send = () =>
    run(async () => {
      setFieldProblem(null)
      if (!url.trim()) return setFieldProblem(t('sources.subscription.linkMissing'))
      try {
        onDone(await sourcesApi.link(row.id, url.trim()))
      } catch (error) {
        setFieldProblem(textOf(error))
      }
    })
  return (
    <Dialog title={t('sources.newLinkTitle')} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void send()
        }}
        data-testid="link-dialog"
      >
        <TextField label={row.google ? t('sources.googleRead.address') : t('sources.subscription.link')} value={url} onChange={setUrl} problem={fieldProblem} testId="link-url" />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={busy} data-testid="link-go">
            {t('common.save')}
          </button>
        </Buttons>
      </form>
    </Dialog>
  )
}

// ---- Holidays ------------------------------------------------------------------------------------------------------------

/**
 * "Feiertage und Ferien" (TIME-17 AC-3, AC-4, AC-8, EC-13, EC-17): a country, any number of its regions (all of them
 * to tick, wrapping, never scrolling sideways), "Schulferien zeigen" where they are known; one row per country, so a
 * country chosen before opens with its choice.
 */
export function HolidaysDialog({ rows, start, onDone, onClose }: { rows: SourceRow[]; start?: SourceRow; onDone: (sources: Sources) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [countries, setCountries] = useState<HolidayCountry[] | null>(null)
  const [country, setCountry] = useState(start?.holidays?.country ?? '')
  const [regions, setRegions] = useState<string[]>(start?.holidays?.regions ?? [])
  const [school, setSchool] = useState(start?.holidays?.school ?? true)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()

  useEffect(() => {
    let live = true
    sourcesApi
      .countries()
      .then((answer) => {
        if (!live) return
        const list = Array.isArray(answer?.countries) ? answer.countries : []
        setCountries(list)
        setCountry((value) => value || list[0]?.code || '')
      })
      .catch((error) => live && setProblem(textOf(error)))
    return () => {
      live = false
    }
  }, [])

  const chosen = countries?.find((entry) => entry.code === country) ?? null
  const pick = (code: string) => {
    setCountry(code)
    const before = rows.find((row) => row.holidays?.country === code)
    setRegions(before?.holidays?.regions ?? [])
    setSchool(before?.holidays?.school ?? true)
  }
  const flip = (code: string) => setRegions((value) => (value.includes(code) ? value.filter((entry) => entry !== code) : [...value, code]))
  const apply = () =>
    run(async () => {
      setProblem(null)
      try {
        onDone(await sourcesApi.holidays(country, regions, !!chosen?.school && school))
      } catch (error) {
        setProblem(textOf(error))
      }
    })

  return (
    <Dialog title={t('sources.holidays.title')} onClose={onClose} medium>
      <div className="space-y-4" data-testid="holidays-dialog">
        <label className="block text-sm">
          <span className="font-medium text-mist-100">{t('sources.holidays.country')}</span>
          <select className="nt-field mt-1" value={country} onChange={(event) => pick(event.target.value)} data-testid="holidays-country">
            {(countries ?? []).map((entry) => (
              <option key={entry.code} value={entry.code}>
                {entry.name}
              </option>
            ))}
          </select>
        </label>
        {chosen && (
          <fieldset className="text-sm">
            <legend className="nt-legend">{t(`sources.holidays.regions.${chosen.code === 'CH' ? 'cantons' : 'states'}`)}</legend>
            <div className="mt-1.5 flex flex-wrap gap-2" data-testid="holidays-regions">
              {chosen.regions.map((region) => {
                const on = regions.includes(region.code)
                return (
                  <button
                    key={region.code}
                    type="button"
                    aria-pressed={on}
                    className="nt-chip"
                    title={region.name}
                    onClick={() => flip(region.code)}
                    data-testid="holidays-region"
                  >
                    <span className="truncate">{region.name}</span>
                  </button>
                )
              })}
            </div>
          </fieldset>
        )}
        {chosen &&
          (chosen.school ? (
            <Switch label={t('sources.holidays.school')} checked={school} onChange={setSchool} testId="holidays-school" />
          ) : (
            <p className="text-xs text-mist-500" data-testid="holidays-no-school">
              {t('sources.holidays.noSchool')}
            </p>
          ))}
        <p className="text-xs text-mist-500">{t('sources.holidays.text')}</p>
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="nt-btn nt-btn-accent" disabled={busy || !chosen} onClick={() => void apply()} data-testid="holidays-go">
            {t('sources.holidays.go')}
          </button>
        </Buttons>
      </div>
    </Dialog>
  )
}

// ---- Disconnecting ------------------------------------------------------------------------------------------------------

/** "Trennen" or "Entfernen" (TIME-15 AC-5): with changes still waiting it says first how many get lost (EC-8). */
export function RemoveDialog({ row, onDone, onClose }: { row: SourceRow; onDone: (sources: Sources) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const kindName = useKindName()
  const [waiting, setWaiting] = useState(row.waiting)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, run] = useOnce()
  const removing = row.kind === 'subscription' || row.kind === 'holidays'
  const go = () =>
    run(async () => {
      setProblem(null)
      try {
        onDone(await sourcesApi.remove(row.id, waiting > 0))
      } catch (error) {
        // More changes came meanwhile: say how many, and ask again.
        if (error instanceof ApiError && error.code === 'changes_waiting') return setWaiting(Number(error.values.count ?? 1))
        setProblem(textOf(error))
      }
    })
  const name = row.kind === 'holidays' || row.kind === 'subscription' ? (row.calendars[0]?.name ?? row.label) : kindName(row.kind)
  return (
    <Dialog title={t(removing ? 'sources.removeTitle' : 'sources.disconnectTitle', { name })} onClose={onClose}>
      <div className="space-y-4" data-testid="remove-dialog">
        <p className="text-sm text-mist-200">{removing ? t('sources.removeText') : t('sources.below')}</p>
        {waiting > 0 && (
          <p role="alert" className="rounded-[9px] border border-warn-500/30 bg-warn-wash px-3 py-2 text-sm text-warn-500" data-testid="remove-waiting">
            {t('sources.waitingLost', { count: waiting, kind: kindName(row.kind) })}
          </p>
        )}
        <Trouble text={problem} />
        <Buttons>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="nt-btn nt-btn-danger" disabled={busy} onClick={() => void go()} data-testid="remove-go">
            {removing ? t('sources.remove') : t('sources.disconnect')}
          </button>
        </Buttons>
      </div>
    </Dialog>
  )
}
