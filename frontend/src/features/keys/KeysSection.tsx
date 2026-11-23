/**
 * Settings › My account › Connections: "Schlüssel für Programme" (TIME-26), built like nexnotes' "API-Token". A key gets
 * a name, "lesen" or "lesen, schreiben", all calendars or some, a run-out (90 days chosen) and "für MCP", and is shown
 * exactly once above the list with "Kopieren" (AC-1). Each row names its right, MCP as a mark, its calendars, the
 * run-out and the last use, with "Widerrufen" (AC-4); an expired one says so and is removed the same way (EC-1). Under
 * the list the address and the sentence of AC-7. While the operator has the API off, nothing new and a hint (AC-5).
 */
import { BookOpen, KeyRound } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '../../components/Dialog'
import { Field, Problem } from '../../components/Field'
import { copyText } from '../../lib/copy'
import { problemText } from '../../lib/errors'
import { day } from '../../lib/format'
import { ago } from '../../lib/time'
import { Card } from '../../pages/settings/ui'
import { newRef } from '../calendars/api'
import { useCalendars } from '../calendars/store'
import { Swatch } from '../calendars/Swatch'
import { useOffline } from '../offline/connection'
import { DEFAULT_DAYS, EXPIRY_CHOICES, keysApi, type KeyEntry, type KeyLevel, type KeyList, type MadeKey } from './api'

export function KeysSection() {
  const { t, i18n } = useTranslation()
  const [state, setState] = useState<KeyList | null>(null)
  const [problem, setProblem] = useState('')
  const [making, setMaking] = useState(false)
  const [made, setMade] = useState<MadeKey | null>(null)
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const [revoking, setRevoking] = useState<KeyEntry | null>(null)
  const offline = useOffline()

  const load = useCallback(() => keysApi.list().then((found) => setState(Array.isArray(found?.keys) ? found : null), (error) => setProblem(problemText(error))), [])
  useEffect(() => {
    void load()
  }, [load])

  const revoke = async (entry: KeyEntry) => {
    setProblem('')
    try {
      await keysApi.revoke(entry.id)
      if (made?.made.id === entry.id) setMade(null)
      setRevoking(null)
      await load()
    } catch (error) {
      setRevoking(null)
      setProblem(problemText(error))
    }
  }

  const full = state ? state.keys.length >= state.max_keys : false
  return (
    <Card id="keys" icon={KeyRound} title={t('keys.title')} text={t('keys.text')}>
      {state && !state.api_allowed && (
        <p className="rounded-[9px] border border-warn-500/30 bg-[var(--color-warn-wash)] px-3 py-2 text-sm text-warn-500" data-testid="keys-off">
          {t('keys.off')}
        </p>
      )}
      <Problem text={problem} />
      {made?.token && (
        <div className="space-y-2 rounded-[12px] border border-accent-500/40 bg-[var(--color-accent-wash)] p-3" data-testid="keys-made">
          <p className="text-sm text-mist-100">{t('keys.made', { name: made.made.name })}</p>
          <div className="flex flex-wrap gap-2">
            {/* Whole, wrapping: on a phone it can be read off when copying fails (review, 2026-11-23). */}
            <code className="min-w-0 flex-1 rounded-[8px] border border-ink-700 bg-ink-900 px-2.5 py-2 font-mono text-[12.5px] break-all text-mist-300 select-all" data-testid="keys-secret">
              {made.token}
            </code>
            <button
              type="button"
              className="nt-btn nt-btn-accent shrink-0"
              onClick={() =>
                void copyText(made.token!).then((done) => {
                  setCopied(done)
                  setCopyFailed(!done)
                })
              }
              data-testid="keys-copy"
            >
              {copied ? t('common.copied') : t('common.copy')}
            </button>
          </div>
          {copyFailed && (
            <p role="alert" className="text-xs text-warn-500" data-testid="keys-copy-failed">
              {t('keys.copyFailed')}
            </p>
          )}
        </div>
      )}
      {state && state.keys.length === 0 && (
        <p className="text-sm text-mist-500" data-testid="keys-none">
          {t('keys.none')}
        </p>
      )}
      {state && state.keys.length > 0 && (
        <ul className="flex flex-col" data-testid="keys-list">
          {state.keys.map((entry) => (
            <KeyRow key={entry.id} entry={entry} lang={i18n.language} onRevoke={() => setRevoking(entry)} />
          ))}
        </ul>
      )}
      {state && (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className="nt-btn nt-btn-ghost"
            disabled={!state.api_allowed || full || offline}
            onClick={() => {
              setProblem('')
              setMaking(true)
            }}
            data-testid="keys-new"
          >
            {t('keys.new')}
          </button>
          {full && <span className="text-xs text-mist-500">{t('keys.count', { count: state.keys.length, max: state.max_keys })}</span>}
        </div>
      )}
      {state && (
        <div className="space-y-1 text-xs text-mist-500">
          <p data-testid="keys-below">{t('keys.below')}</p>
          <p>
            {t('keys.address')}: <code className="font-mono break-all text-mist-300" data-testid="keys-address">{state.address}/api/v1</code>
          </p>
          <p>{t('keys.mcpAddress', { address: `${state.address}/api/mcp` })}</p>
          <a href="/api/v1/docs" target="_blank" rel="noopener" className="inline-flex items-center gap-1.5 text-accent-400 hover:underline" data-testid="keys-docs">
            <BookOpen className="h-3.5 w-3.5" aria-hidden />
            {t('keys.docs')}
          </a>
        </div>
      )}
      {making && state && (
        <NewKeyDialog
          mcpAllowed={state.mcp_allowed}
          onClose={() => setMaking(false)}
          onMade={(key) => {
            setMaking(false)
            setCopied(false)
            if (key.token) setMade(key)
            void load()
          }}
        />
      )}
      {revoking && (
        <Dialog title={t(revoking.expired ? 'keys.removeTitle' : 'keys.revokeTitle', { name: revoking.name })} onClose={() => setRevoking(null)}>
          <p className="text-sm text-mist-300">{t(revoking.expired ? 'keys.removeText' : 'keys.revokeText')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setRevoking(null)}>
              {t('common.cancel')}
            </button>
            <button type="button" className="nt-btn nt-btn-danger" onClick={() => void revoke(revoking)} data-testid="keys-revoke-confirm">
              {t(revoking.expired ? 'keys.remove' : 'keys.revoke')}
            </button>
          </div>
        </Dialog>
      )}
    </Card>
  )
}

function KeyRow({ entry, lang, onRevoke }: { entry: KeyEntry; lang: string; onRevoke: () => void }) {
  const { t } = useTranslation()
  const calendars = entry.calendars ? entry.calendars.map((calendar) => calendar.name).join(', ') : t('keys.calendarsAll')
  const runOut = entry.expired ? t('keys.expiry.expired') : entry.expires_at ? t('keys.expiry.on', { date: day(entry.expires_at, lang) }) : t('keys.expiry.never')
  const used = entry.last_used_at ? ago(new Date(entry.last_used_at).getTime(), lang) : t('keys.neverUsed')
  return (
    <li className="flex items-center gap-3 border-b border-ink-800 py-2.5 last:border-0" data-testid="key-row">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-center gap-2 text-sm text-mist-100">
          <span className="truncate" data-testid="key-name">
            {entry.name}
          </span>
          {entry.mcp && (
            <span className="nt-mark shrink-0" data-testid="key-mcp">
              MCP
            </span>
          )}
        </span>
        <span className="text-xs break-words text-mist-500" data-testid="key-meta">
          {[t(`keys.level.${entry.level}`), calendars].join(' · ')}
        </span>
        <span className="text-xs text-mist-500">
          <span className={entry.expired ? 'text-warn-500' : ''} data-testid="key-expiry">
            {runOut}
          </span>
          {' · '}
          <span data-testid="key-used">{t('keys.lastUsed', { when: used })}</span>
        </span>
      </span>
      <button type="button" className="shrink-0 text-[13px] text-bad-500 hover:underline" onClick={onRevoke} data-testid="key-revoke">
        {entry.expired ? t('keys.remove') : t('keys.revoke')}
      </button>
    </li>
  )
}

function NewKeyDialog({ mcpAllowed, onClose, onMade }: { mcpAllowed: boolean; onClose: () => void; onMade: (key: MadeKey) => void }) {
  const { t } = useTranslation()
  const { list } = useCalendars()
  const [name, setName] = useState('')
  const [level, setLevel] = useState<KeyLevel>('read')
  const [mcp, setMcp] = useState(false)
  const [limited, setLimited] = useState(false)
  const [chosen, setChosen] = useState<number[]>([])
  const [days, setDays] = useState<number | null>(DEFAULT_DAYS)
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)
  // One reference for this dialog: a second click makes no second key (EC-16).
  const ref = useRef(newRef())

  const ready = name.trim() !== '' && (!limited || chosen.length > 0) && !busy
  const make = async () => {
    if (!ready) return
    setBusy(true)
    setProblem('')
    try {
      onMade(await keysApi.make({ name: name.trim(), level, mcp, calendar_ids: limited ? chosen : null, days, client_ref: ref.current }))
    } catch (error) {
      setProblem(problemText(error))
      setBusy(false)
    }
  }
  const toggle = (id: number, on: boolean) => setChosen((before) => (on ? [...before, id] : before.filter((other) => other !== id)))

  return (
    <Dialog title={t('keys.dialog.title')} onClose={onClose} medium>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void make()
        }}
        data-testid="key-dialog"
      >
        <Field label={t('keys.dialog.name')} value={name} onChange={setName} maxLength={100} hint={t('keys.dialog.namePlaceholder')} />
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-mist-100">{t('keys.dialog.right')}</legend>
          {(['read', 'write'] as const).map((choice) => (
            <label key={choice} className="flex cursor-pointer gap-3 rounded-[12px] border border-ink-700 bg-ink-900 px-3 py-2.5">
              <input type="radio" name="key-level" value={choice} checked={level === choice} onChange={() => setLevel(choice)} className="mt-1 accent-[var(--color-accent-500)]" data-testid={`key-level-${choice}`} />
              <span>
                <span className="block text-sm text-mist-100">{t(`keys.dialog.${choice}`)}</span>
                <span className="block text-xs text-mist-500">{t(`keys.dialog.${choice}Hint`)}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <label className="flex cursor-pointer items-start gap-3 text-sm">
          <input
            type="checkbox"
            checked={mcp}
            disabled={!mcpAllowed}
            onChange={(event) => {
              setMcp(event.target.checked)
              // A key for an AI tool reads only, from the start (decided after the review, 2026-11-23).
              if (event.target.checked) setLevel('read')
            }}
            className="mt-1 accent-[var(--color-accent-500)]"
            data-testid="key-mcp-choice"
          />
          <span>
            <span className="block text-mist-100">{t('keys.dialog.mcp')}</span>
            <span className="block text-xs text-mist-500">{mcpAllowed ? t('keys.dialog.mcpHint') : t('keys.dialog.mcpOff')}</span>
          </span>
        </label>
        {mcp && level === 'write' && (
          <p className="rounded-[9px] border border-warn-500/30 bg-[var(--color-warn-wash)] px-3 py-2 text-xs text-warn-500" data-testid="key-mcp-write-hint">
            {t('keys.dialog.mcpWriteHint')}
          </p>
        )}
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium text-mist-100">{t('keys.dialog.calendars')}</legend>
          <label className="flex cursor-pointer items-center gap-3 text-sm text-mist-100">
            <input type="radio" name="key-scope" checked={!limited} onChange={() => setLimited(false)} className="accent-[var(--color-accent-500)]" data-testid="key-scope-all" />
            {t('keys.dialog.calendarsAll')}
          </label>
          <p className="pl-7 text-xs text-mist-500" data-testid="key-hidden-note">
            {t('keys.dialog.hiddenNote')}
          </p>
          <label className="flex cursor-pointer items-center gap-3 text-sm text-mist-100">
            <input type="radio" name="key-scope" checked={limited} onChange={() => setLimited(true)} className="accent-[var(--color-accent-500)]" data-testid="key-scope-some" />
            {t('keys.dialog.calendarsSome')}
          </label>
          {limited && (
            <ul className="nt-scroll max-h-48 overflow-y-auto rounded-[12px] border border-ink-700 bg-ink-900 px-3 py-2" data-testid="key-calendars">
              {(list?.calendars ?? []).map((calendar) => (
                <li key={calendar.id}>
                  <label className="flex cursor-pointer items-center gap-2 py-1 text-sm text-mist-100">
                    <input type="checkbox" checked={chosen.includes(calendar.id)} onChange={(event) => toggle(calendar.id, event.target.checked)} className="accent-[var(--color-accent-500)]" data-testid="key-calendar" data-calendar={calendar.id} />
                    <Swatch color={calendar.color} icon={calendar.icon} />
                    <span className="truncate">{calendar.name}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
        <label className="block text-sm">
          <span className="font-medium text-mist-100">{t('keys.dialog.expiry')}</span>
          <select className="nt-field mt-1" value={days === null ? 'never' : String(days)} onChange={(event) => setDays(event.target.value === 'never' ? null : Number(event.target.value))} data-testid="key-days">
            {EXPIRY_CHOICES.map((choice) => (
              <option key={String(choice)} value={choice === null ? 'never' : String(choice)}>
                {choice === null ? t('keys.dialog.never') : t(`keys.dialog.days_${choice}`)}
              </option>
            ))}
          </select>
        </label>
        <Problem text={problem} />
        <div className="flex justify-end gap-2">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={!ready} data-testid="key-make">
            {t('keys.dialog.make')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/** Settings › Server › Accounts: how many keys each account holds (AC-5), never a key, its name or its calendars. */
export function AllKeysCard() {
  const { t } = useTranslation()
  const [data, setData] = useState<Awaited<ReturnType<typeof keysApi.overview>> | null>(null)
  useEffect(() => {
    keysApi.overview().then((found) => setData(Array.isArray(found?.accounts) ? found : null), () => setData(null))
  }, [])
  if (!data) return null
  return (
    <Card id="all-keys" icon={KeyRound} title={t('keys.admin.title')} text={t('keys.admin.text')}>
      {data.accounts.length === 0 ? (
        <p className="text-sm text-mist-500" data-testid="all-keys-none">
          {t('keys.admin.none')}
        </p>
      ) : (
        <ul className="flex flex-col" data-testid="all-keys">
          {data.accounts.map((row) => (
            <li key={row.account.id} className="flex items-center justify-between gap-3 border-b border-ink-800 py-2 text-sm last:border-0" data-testid="all-keys-row">
              <span className="min-w-0 truncate text-mist-100">{row.account.name}</span>
              <span className="shrink-0 text-xs text-mist-500">{t('keys.admin.row', { count: row.keys, mcp: row.mcp })}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
