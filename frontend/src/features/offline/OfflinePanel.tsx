/**
 * The side panel without a connection (TIME-28 AC-3): the event as it was kept, to read only (title, time, place,
 * notes, its calendar), with "Ohne Verbindung nur lesen. Ändern geht wieder mit Verbindung." as the mock-up says it.
 * "Busy only" shows a block, as everywhere. Titles, places and notes are text, never markup.
 */
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useDisplay } from '../../lib/useDisplay'
import { LinkedText } from '../calendars/links'
import { Swatch } from '../calendars/Swatch'
import { useCalendars, usePanel, type PanelTarget } from '../calendars/store'
import { addDays, clockText, dayName, wallOf } from '../calendars/time'
import { keptOccurrence } from './kept'
import { useKept } from './snapshot'

export function OfflinePanel({ target }: { target: Extract<PanelTarget, { kind: 'open' }> }) {
  const { t } = useTranslation()
  const { close } = usePanel()
  const { zone, twelve, language } = useDisplay()
  const { snapshot } = useKept()
  const { list } = useCalendars()
  const row = snapshot ? keptOccurrence(snapshot, target.id, target.occurrence) : null
  const calendar = row ? (list?.calendars.find((entry) => entry.id === row.calendar_id) ?? null) : null
  let when = ''
  if (row && row.all_day) {
    when = row.end > row.start ? `${dayName(row.start, language)} – ${dayName(row.end, language)} · ${t('rule.allDay')}` : `${dayName(row.start, language)} · ${t('rule.allDay')}`
  } else if (row) {
    const start = wallOf(Date.parse(row.start), zone)
    const end = wallOf(Date.parse(row.end), zone)
    const endText = end.date === start.date || (end.date === addDays(start.date, 1) && end.time === '00:00') ? clockText(end.time, language, twelve) : `${dayName(end.date, language)} ${clockText(end.time, language, twelve)}`
    when = `${dayName(start.date, language)} · ${clockText(start.time, language, twelve)}–${endText}`
  }
  return (
    <div className="fixed inset-0 z-40 flex justify-end" data-testid="event-panel">
      <button type="button" tabIndex={-1} aria-label={t('panel.closeBehind')} className="flex-1 bg-scrim" onClick={close} />
      <aside aria-label={row ? (row.busy_only ? t('panel.busyBlock') : row.title) : t('offline.panelTitle')} className="flex h-full w-full max-w-[460px] flex-col border-l border-ink-700 bg-ink-850 shadow-2xl">
        <div className="flex items-center gap-2.5 border-b border-ink-700 px-5 py-3">
          {calendar && <Swatch color={calendar.color} icon={calendar.icon} />}
          <span className="min-w-0 flex-1 truncate text-sm text-mist-200" data-testid="panel-head">
            {calendar?.name ?? ''}
          </span>
          <button type="button" aria-label={t('common.close')} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100" onClick={close} data-testid="panel-close">
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>
        <div className="nt-scroll min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4" data-testid="offline-panel">
          {row ? (
            <>
              <strong className="nt-wrap block text-lg text-mist-100">{row.busy_only ? t('panel.busyBlock') : row.title}</strong>
              <span className="block text-sm text-mist-500">{when}</span>
              {!row.busy_only && row.location && <p className="nt-wrap text-sm text-mist-200">{row.location}</p>}
              {!row.busy_only && row.notes && (
                <p className="text-sm break-words whitespace-pre-wrap text-mist-200" data-testid="offline-notes">
                  <LinkedText text={row.notes} />
                </p>
              )}
            </>
          ) : (
            <p className="text-sm text-mist-300">{t('offline.notKept')}</p>
          )}
          <p className="rounded-[12px] border border-warn-500/40 bg-warn-wash px-3 py-2 text-sm text-warn-500" data-testid="read-only-reason">
            {t('offline.panel')}
          </p>
        </div>
        <div className="flex border-t border-ink-700 px-5 py-3">
          <button type="button" className="nt-btn nt-btn-accent ml-auto" onClick={close}>
            {t('common.close')}
          </button>
        </div>
      </aside>
    </div>
  )
}
