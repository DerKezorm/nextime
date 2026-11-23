/**
 * "Calendars and accounts" (TIME-15 AC-10): one row per source with its kind, the account or address with the chosen
 * calendars, and its state ("abgeglichen vor 1 Minute", or a problem in orange with the button that mends it); per row
 * "Kalender wählen" or "Ändern", and "Trennen" or "Entfernen"; "Kalender hinzufügen" on top, the sentence about
 * disconnecting below (AC-5). Without a source a short hint and the button (EC-9).
 *
 * Google sends the browser back here with what happened in the address (TIME-16 AC-2, EC-6, EC-7, AC-8): the choice of
 * calendars opens, or a sentence says why nothing is connected.
 */
import { Plus } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { problemText } from '../../lib/errors'
import { showNotice } from '../../lib/notice'
import { ago } from '../../lib/time'
import { sourcesApi, type HolidayCountry, type SourceRow } from './api'
import { AddCalendar, ChooseSource } from './AddCalendar'
import { GoogleDialog, HolidaysDialog, LinkDialog, ReconnectDialog, RemoveDialog } from './Dialogs'
import { useKindName, useNames, useSources, useStamp } from './store'

type Open =
  | { kind: 'add' }
  | { kind: 'choose'; sourceId: number; connected?: boolean; note?: string | null }
  | { kind: 'reconnect' | 'link' | 'remove' | 'holidays' | 'google'; row: SourceRow }

/** What came back from Google in the address, read once. */
function fromGoogle(params: URLSearchParams): { open: Open | null; message: { tone: 'ok' | 'bad' | 'plain'; key: string } | null } {
  const google = params.get('google')
  const choose = Number(params.get('choose') || 0)
  if (choose) return { open: { kind: 'choose', sourceId: choose, connected: google !== 'exists', note: google === 'exists' ? 'exists' : null }, message: null }
  if (!google) return { open: null, message: null }
  if (google === 'reconnected') return { open: null, message: { tone: 'ok', key: 'reconnected' } }
  if (['denied', 'expired', 'failed', 'other_account', 'exists'].includes(google)) return { open: null, message: { tone: 'bad', key: google } }
  return { open: null, message: null }
}

/** `calendars`: the account's calendars, between the button at the top and the sources (Settings). */
export function SourcesList({ calendars }: { calendars?: ReactNode } = {}) {
  const { t, i18n } = useTranslation()
  const { data, failed, take } = useSources()
  const kindName = useKindName()
  const stamp = useStamp()
  const names = useNames()
  const [params, setParams] = useSearchParams()
  const [first] = useState(() => fromGoogle(params))
  const [open, setOpen] = useState<Open | null>(first.open)
  // A success says so at the bottom of the page, as in nexnotes; what went wrong stays in the page.
  const [message] = useState(first.message?.tone === 'ok' ? null : first.message)
  useEffect(() => {
    if (first.message?.tone === 'ok') showNotice({ text: t(`sources.googleBack.${first.message.key}`) })
  }, [first, t])
  const [problem, setProblem] = useState<string | null>(null)
  const [countries, setCountries] = useState<HolidayCountry[] | null>(null)
  const busy = useRef(new Set<number>()).current

  // The answer of Google is read; the address is clean again, so a reload does not open it twice.
  useEffect(() => {
    if (params.has('google') || params.has('choose') || params.has('source')) setParams({}, { replace: true })
  }, [params, setParams])

  const holidaysShown = !!data?.sources.some((row) => row.kind === 'holidays')
  useEffect(() => {
    if (!holidaysShown || countries) return
    sourcesApi
      .countries()
      .then((answer) => setCountries(Array.isArray(answer?.countries) ? answer.countries : []))
      .catch(() => setCountries([]))
  }, [holidaysShown, countries])

  const refresh = async (row: SourceRow) => {
    if (busy.has(row.id)) return
    busy.add(row.id)
    setProblem(null)
    try {
      take(await sourcesApi.refresh(row.id))
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      busy.delete(row.id)
    }
  }

  const rows = data?.sources ?? []
  const expired = rows.filter((row) => row.state === 'expired' && row.calendars.length > 0)

  return (
    <div className="space-y-5" data-testid="sources">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="nt-btn nt-btn-accent ml-auto" onClick={() => setOpen({ kind: 'add' })} data-testid="sources-add">
          <Plus className="h-4 w-4" aria-hidden />
          {t('calendars.add')}
        </button>
      </div>
      {message && (
        <p
          role="status"
          className={'rounded-[9px] border px-3 py-2 text-sm ' + (message.tone === 'ok' ? 'border-ok-500/30 bg-ok-wash text-ok-500' : 'border-bad-500/30 bg-bad-500/10 text-bad-500')}
          data-testid="google-message"
        >
          {t(`sources.googleBack.${message.key}`)}
        </p>
      )}
      {problem && (
        <p role="alert" className="rounded-[9px] border border-bad-500/30 bg-bad-500/10 px-3 py-2 text-sm text-bad-500">
          {problem}
        </p>
      )}
      {failed && !data && (
        <p role="alert" className="text-sm text-bad-500">
          {t('views.loadFailed')}
        </p>
      )}
      {calendars}
      <h3 className="pt-2 text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase" data-testid="sources-head">
        {t('sources.head')}
      </h3>
      {data && rows.length === 0 && (
        // Only when no source is connected; "Kalender hinzufügen" stands at the top already.
        <div className="nt-empty" data-testid="sources-empty">
          <p>{t('sources.empty')}</p>
        </div>
      )}
      {rows.length > 0 && (
        <ul className="divide-y divide-line overflow-hidden rounded-[12px] border border-ink-700 bg-ink-850" data-testid="source-rows">
          {rows.map((row) => (
            rowView(row, countries, setOpen, () => void refresh(row))
          ))}
        </ul>
      )}
      {expired.map((row) => (
        <p key={row.id} role="status" className="rounded-[9px] border border-warn-500/30 bg-warn-wash px-3 py-2 text-sm text-warn-500" data-testid="expired-hint">
          {t('sources.expiredHint', {
            kind: kindName(row.kind),
            names: names(row.calendars.map((entry) => entry.name), true),
            when: stamp(row.synced_at),
            count: row.calendars.length,
          })}
        </p>
      ))}
      {data && data.events_total > data.events_smooth_up_to && (
        <p className="text-xs text-warn-500" data-testid="events-over">
          {t('sources.eventsOver', { total: data.events_total.toLocaleString(i18n.language), limit: data.events_smooth_up_to.toLocaleString(i18n.language) })}
        </p>
      )}
      <p className="text-xs text-mist-500" data-testid="sources-below">
        {t('sources.below')}
      </p>
      {open?.kind === 'add' && <AddCalendar onClose={() => setOpen(null)} />}
      {open?.kind === 'choose' && (
        <ChooseSource sourceId={open.sourceId} connected={open.connected} note={open.note === 'exists' ? t('sources.exists') : open.note} onClose={() => setOpen(null)} />
      )}
      {open?.kind === 'reconnect' && <ReconnectDialog row={open.row} onDone={(answer) => (take(answer), setOpen(null), showNotice({ text: t('sources.reconnected') }))} onClose={() => setOpen(null)} />}
      {open?.kind === 'link' && <LinkDialog row={open.row} onDone={(answer) => (take(answer), setOpen(null))} onClose={() => setOpen(null)} />}
      {open?.kind === 'remove' && <RemoveDialog row={open.row} onDone={(answer) => (take(answer), setOpen(null))} onClose={() => setOpen(null)} />}
      {open?.kind === 'holidays' && <HolidaysDialog rows={rows} start={open.row} onDone={(answer) => (take(answer), setOpen(null))} onClose={() => setOpen(null)} />}
      {open?.kind === 'google' && <GoogleDialog sourceId={open.row.id} onClose={() => setOpen(null)} />}
    </div>
  )

  function rowView(row: SourceRow, countries: HolidayCountry[] | null, onOpen: (open: Open) => void, onRefresh: () => void) {
    const state = stateOf(row)
    const fix = fixOf(row)
    const line = lineOf(row, countries)
    const kind = row.kind === 'subscription' && row.google ? t('sources.kinds.googleRead') : kindName(row.kind)
    return (
      <li key={row.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3" data-testid="source-row" data-kind={row.kind}>
        <span className="w-24 shrink-0 text-[11px] font-semibold tracking-wider text-mist-500 uppercase" data-testid="source-kind">
          {kind}
        </span>
        <div className="flex min-w-0 flex-[1_1_12rem] flex-col gap-0.5">
          <span className="truncate text-sm font-medium text-mist-100" title={line} data-testid="source-line">
            {line}
          </span>
          <span className={'text-xs ' + (state.warn ? 'text-warn-500' : 'text-mist-500')} data-testid="source-state" data-warn={state.warn ? 'true' : 'false'}>
            {state.text}
          </span>
          {row.new > 0 && (
            <span className="text-xs text-accent-400" data-testid="source-new">
              {t('sources.newCalendars', { count: row.new })}
            </span>
          )}
          {row.unreadable > 0 && (
            <span className="text-xs text-mist-500" data-testid="source-unreadable">
              {t('sources.unreadable', { count: row.unreadable })}
            </span>
          )}
          {row.waiting > 0 && (
            <span className="text-xs text-mist-500" data-testid="source-waiting">
              {t('sources.waitingCount', { count: row.waiting })}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {fix === 'reconnect' && (
            <button type="button" className="nt-btn nt-btn-accent h-9 px-3" onClick={() => onOpen(row.kind === 'google' ? { kind: 'google', row } : { kind: 'reconnect', row })} data-testid="source-reconnect">
              {t('sources.reconnect')}
            </button>
          )}
          {fix === 'refresh' && (
            <button type="button" className="nt-btn nt-btn-accent h-9 px-3" onClick={onRefresh} data-testid="source-refresh">
              {t('sources.refresh')}
            </button>
          )}
          {row.kind === 'subscription' && row.state !== 'ok' && (row.google || row.problem === 'subscription_gone') && (
            <button type="button" className="nt-btn nt-btn-ghost h-9 px-3" onClick={() => onOpen({ kind: 'link', row })} data-testid="source-link">
              {t('sources.newLink')}
            </button>
          )}
          {(row.kind === 'icloud' || row.kind === 'caldav' || row.kind === 'google') && (
            <button type="button" className="nt-btn nt-btn-ghost h-9 px-3" onClick={() => onOpen({ kind: 'choose', sourceId: row.id })} data-testid="source-choose">
              {t('sources.choose')}
            </button>
          )}
          {row.kind === 'holidays' && (
            <button type="button" className="nt-btn nt-btn-ghost h-9 px-3" onClick={() => onOpen({ kind: 'holidays', row })} data-testid="source-change">
              {t('sources.change')}
            </button>
          )}
          <button type="button" className="nt-btn nt-btn-ghost h-9 px-3" onClick={() => onOpen({ kind: 'remove', row })} data-testid="source-remove">
            {row.kind === 'subscription' || row.kind === 'holidays' ? t('sources.remove') : t('sources.disconnect')}
          </button>
        </div>
      </li>
    )
  }

  /** "familie@example.com · Arbeit, Persönlich", "Müllabfuhr · abfall.example.com", "Deutschland · Nordrhein-Westfalen,
   * Bayern, mit Schulferien" (AC-10, TIME-17 AC-5, AC-6). */
  function lineOf(row: SourceRow, countries: HolidayCountry[] | null): string {
    if (row.kind === 'holidays' && row.holidays) {
      const country = countries?.find((entry) => entry.code === row.holidays!.country)
      const regions = row.holidays.regions.map((code) => country?.regions.find((region) => region.code === code)?.name ?? code)
      const parts = [regions.length ? regions.join(', ') : t('sources.holidays.nationwide')]
      if (row.holidays.school) parts.push(t('sources.holidays.withSchool'))
      return `${country?.name ?? row.holidays.country} · ${parts.join(', ')}`
    }
    if (row.kind === 'subscription') return [row.calendars[0]?.name, row.label].filter(Boolean).join(' · ')
    const chosen = row.calendars.map((entry) => entry.name).join(', ')
    return chosen ? `${row.label} · ${chosen}` : row.label
  }

  /** The state in words (AC-10, EC-1, EC-12, TIME-16 EC-5, TIME-17 EC-1); orange for a problem. */
  function stateOf(row: SourceRow): { text: string; warn: boolean } {
    if (row.loading) return { text: t('sources.state.loading'), warn: false }
    if (row.state === 'paused') return { text: t('sources.state.paused'), warn: true }
    if (row.state === 'expired' && row.kind !== 'subscription') return { text: t('sources.state.expired', { when: stamp(row.since) }), warn: true }
    if (row.kind === 'subscription' && row.state !== 'ok') return { text: t('sources.state.stale', { when: stamp(row.synced_at ?? row.since) }), warn: true }
    if (row.state === 'unreachable') return { text: t('sources.state.unreachable', { when: stamp(row.synced_at) }), warn: true }
    if (row.kind === 'holidays') return { text: t('sources.state.background'), warn: false }
    if (!row.synced_at) return { text: t('sources.state.never'), warn: false }
    return { text: t('sources.state.synced', { when: ago(Date.parse(row.synced_at), i18n.language) }), warn: false }
  }
}

/** The button that mends a problem: "Neu verbinden" for an access that expired, "Erneut holen" otherwise. */
function fixOf(row: SourceRow): 'reconnect' | 'refresh' | null {
  if (row.loading || row.state === 'ok' || row.state === 'paused') return null
  if (row.state === 'expired' && (row.kind === 'icloud' || row.kind === 'caldav' || row.kind === 'google')) return 'reconnect'
  return 'refresh'
}
