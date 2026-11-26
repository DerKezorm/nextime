/**
 * Settings › "Calendars and accounts" (TIME-15 AC-10, TIME-16 AC-8, TIME-17 AC-5, AC-6): first the account's calendars
 * by group as in the sidebar, own ones first, each with its marks, the eye and the menu (decided while testing, 2026-11-23:
 * "müssten da nicht meine Kalender stehen?"); then the connected sources with their state, and "Add calendar" once at
 * the top (its way "Eigener Kalender" makes a new one); below, the way to "Mitnehmen und umziehen" (TIME-24). Only cards
 * and the kit's buttons (plan "Einstellungen", page frame; gallery, E39): "Kalender" and "Konten und Abos".
 */
import { ArrowRightLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { CalendarList } from '../../features/calendars/CalendarList'
import { SourcesList } from '../../features/sources/SourcesList'
import { Button, Buttons } from '../../vendor/nexui'

export function CalendarsArea() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <div data-testid="calendars-area">
      <SourcesList
        calendars={<CalendarList settings />}
        more={
          <Buttons>
            <Button kind="secondary" icon={ArrowRightLeft} onClick={() => navigate('/settings/move')} testId="to-move">
              {t('move.link')}
            </Button>
          </Buttons>
        }
      />
    </div>
  )
}
