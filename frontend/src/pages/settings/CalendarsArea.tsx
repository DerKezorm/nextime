/**
 * Settings › "Calendars and accounts" (TIME-15 AC-10, TIME-16 AC-8, TIME-17 AC-5, AC-6): first the account's calendars
 * by group as in the sidebar, own ones first, each with its marks, the eye and the menu (decided while testing, 2026-11-23:
 * "müssten da nicht meine Kalender stehen?"); then the connected sources with their state, and "Add calendar" once at
 * the top (its way "Eigener Kalender" makes a new one); below, the way to "Mitnehmen und umziehen" (TIME-24).
 */
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { CalendarList } from '../../features/calendars/CalendarList'
import { SourcesList } from '../../features/sources/SourcesList'

export function CalendarsArea() {
  const { t } = useTranslation()
  return (
    <div className="space-y-4" data-testid="calendars-area">
      <SourcesList calendars={<CalendarList settings />} />
      <Link to="/settings/move" className="nt-btn nt-btn-ghost w-fit" data-testid="to-move">
        {t('move.link')}
      </Link>
    </div>
  )
}
