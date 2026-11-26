/**
 * "Calendars and accounts" (TIME-15 AC-10): one row per source with its kind, the account or address with the chosen
 * calendars, and its state ("abgeglichen vor 1 Minute", or a problem in orange with the button that mends it); per row
 * "Kalender wählen" or "Ändern", and "Trennen" or "Entfernen"; "Kalender hinzufügen" on top, the sentence about
 * disconnecting below (AC-5). Without a source a short hint and the button (EC-9).
 *
 * Google sends the browser back here with what happened in the address (TIME-16 AC-2, EC-6, EC-7, AC-8): the choice of
 * calendars opens, or a sentence says why nothing is connected.
 */
import { CalendarDays, Cloud, ListChecks } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'

import { problemText } from '../../lib/errors'
import { Button, Buttons, Card, Notice, Rows } from '../../vendor/nexui'
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
export function SourcesList({ calendars, more }: { calendars?: ReactNode; more?: ReactNode } = {}) {
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
    <div className="flex flex-col gap-6" data-testid="sources">
      <Card id="calendars" icon={CalendarDays} title={t('calendars.settingsCard.title')} subtitle={t('calendars.settingsCard.text')}>
        <Buttons>
          <Button kind="primary" icon="Plus" onClick={() => setOpen({ kind: 'add' })} testId="sources-add">
            {t('calendars.add')}
          </Button>
        </Buttons>
        {message && (
          <Notice tone={message.tone === 'ok' ? 'ok' : 'danger'} testId="google-message">
            {t(`sources.googleBack.${message.key}`)}
          </Notice>
        )}
        {calendars}
      </Card>
      <Card id="calendar-sources" icon={Cloud} title={t('sources.head')} subtitle={t('sources.below')} testId="sources-head">
        {problem && <Notice tone="danger">{problem}</Notice>}
        {failed && !data && <Notice tone="danger">{t('views.loadFailed')}</Notice>}
        {data && rows.length === 0 && (
          // Only when no source is connected; "Kalender hinzufügen" stands in the card above.
          <p className="m-0 text-sm text-[var(--nexui-text-muted)]" data-testid="sources-empty">
            {t('sources.empty')}
          </p>
        )}
        {rows.length > 0 && (
          <Rows testId="source-rows">
            {rows.map((row) => rowView(row, countries, setOpen, () => void refresh(row)))}
          </Rows>
        )}
        {expired.map((row) => (
          <Notice key={row.id} tone="warn" testId="expired-hint">
            {t('sources.expiredHint', {
              kind: kindName(row.kind),
              names: names(row.calendars.map((entry) => entry.name), true),
              when: stamp(row.synced_at),
              count: row.calendars.length,
            })}
          </Notice>
        ))}
        {data && data.events_total > data.events_smooth_up_to && (
          <Notice tone="warn" testId="events-over">
            {t('sources.eventsOver', { total: data.events_total.toLocaleString(i18n.language), limit: data.events_smooth_up_to.toLocaleString(i18n.language) })}
          </Notice>
        )}
        {more}
      </Card>
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
    const notes = [
      row.new > 0 ? <span key="new" data-testid="source-new">{t('sources.newCalendars', { count: row.new })}</span> : null,
      row.unreadable > 0 ? <span key="unreadable" data-testid="source-unreadable">{t('sources.unreadable', { count: row.unreadable })}</span> : null,
      row.waiting > 0 ? <span key="waiting" data-testid="source-waiting">{t('sources.waitingCount', { count: row.waiting })}</span> : null,
    ].filter(Boolean)
    return (
      <li key={row.id} className="flex flex-col gap-1 py-2" data-testid="source-row" data-kind={row.kind}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <div className="flex min-w-0 flex-[1_1_12rem] flex-col gap-0.5">
            <span className="text-[11px] font-semibold tracking-wider text-[var(--nexui-text-muted)] uppercase" data-testid="source-kind">
              {kind}
            </span>
            <span className="truncate text-sm font-medium text-[var(--nexui-text)]" title={line} data-testid="source-line">
              {line}
            </span>
            <span className={'text-xs ' + (state.warn ? 'text-[var(--nexui-warn)]' : 'text-[var(--nexui-text-muted)]')} data-testid="source-state" data-warn={state.warn ? 'true' : 'false'}>
              {state.text}
            </span>
            {notes.length > 0 && <span className="flex flex-col text-xs text-[var(--nexui-text-muted)]">{notes}</span>}
          </div>
          <Buttons>
            {fix === 'reconnect' && (
              <Button kind="secondary" size="sm" icon="RefreshCw" onClick={() => onOpen(row.kind === 'google' ? { kind: 'google', row } : { kind: 'reconnect', row })} testId="source-reconnect">
                {t('sources.reconnect')}
              </Button>
            )}
            {fix === 'refresh' && (
              <Button kind="secondary" size="sm" icon="RefreshCw" onClick={onRefresh} testId="source-refresh">
                {t('sources.refresh')}
              </Button>
            )}
            {row.kind === 'subscription' && row.state !== 'ok' && (row.google || row.problem === 'subscription_gone') && (
              <Button kind="secondary" size="sm" icon="Link" onClick={() => onOpen({ kind: 'link', row })} testId="source-link">
                {t('sources.newLink')}
              </Button>
            )}
            {(row.kind === 'icloud' || row.kind === 'caldav' || row.kind === 'google') && (
              <Button kind="secondary" size="sm" icon={ListChecks} onClick={() => onOpen({ kind: 'choose', sourceId: row.id })} testId="source-choose">
                {t('sources.choose')}
              </Button>
            )}
            {row.kind === 'holidays' && (
              <Button kind="secondary" size="sm" icon="Pencil" onClick={() => onOpen({ kind: 'holidays', row })} testId="source-change">
                {t('sources.change')}
              </Button>
            )}
            <Button kind="secondary" size="sm" icon="Trash2" onClick={() => onOpen({ kind: 'remove', row })} testId="source-remove">
              {row.kind === 'subscription' || row.kind === 'holidays' ? t('sources.remove') : t('sources.disconnect')}
            </Button>
          </Buttons>
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
