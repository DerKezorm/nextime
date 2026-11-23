/**
 * An event shown once from several calendars (TIME-14 AC-3) changes in one of them only: before saving, deleting or
 * dragging, nextime asks in which (EC-2), offering only the calendars the member may change (EC-3).
 */
import { useTranslation } from 'react-i18next'

import { Dialog } from '../../components/Dialog'
import type { CalendarEntry, Source } from './api'
import { Swatch } from './Swatch'

export function SourceQuestion({ title, sources, calendars, deleting = false, onChoose, onClose }: {
  title: string
  sources: Source[]
  calendars: CalendarEntry[]
  deleting?: boolean
  onChoose: (source: Source) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const writable = sources.filter((source) => source.writable)
  return (
    <Dialog title={t(deleting ? 'panel.whichSourceDelete' : 'panel.whichSourceChange')} onClose={onClose}>
      <div className="flex flex-col gap-2" data-testid="source-question">
        <p className="text-sm text-mist-200">{t('panel.inCalendars', { title, count: sources.length })}</p>
        {writable.map((source) => {
          const calendar = calendars.find((entry) => entry.id === source.calendar_id)
          return (
            <button key={source.calendar_id} type="button" className="nt-btn nt-btn-ghost justify-start" onClick={() => onChoose(source)} data-testid="source-choice">
              {calendar && <Swatch color={calendar.color} icon={calendar.icon} />}
              {calendar?.name ?? ''}
            </button>
          )
        })}
        <button type="button" className="nt-btn nt-btn-quiet self-end" onClick={onClose}>
          {t('common.cancel')}
        </button>
      </div>
    </Dialog>
  )
}
