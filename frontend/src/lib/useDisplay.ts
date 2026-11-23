/** The account's way of seeing times, ready to use: the choices, the zone shown, 12 or 24 hours (TIME-3). */
import { useTranslation } from 'react-i18next'

import { deviceZone } from '../api/client'
import { useMe } from '../state/auth'
import { DEFAULT_DISPLAY, shownZone, uses12Hours } from './display'

export function useDisplay() {
  const me = useMe()
  const { i18n } = useTranslation()
  const display = me?.display ?? DEFAULT_DISPLAY
  // Read at every draw: a device that changed its zone while the page was open follows at the next draw, at the
  // latest after reloading (EC-11).
  const device = deviceZone()
  const zone = shownZone(display, device)
  const twelve = uses12Hours(display, i18n.language)
  // For the times of the pages around the calendar (backups, the log): the same clock and zone as everywhere.
  const timeOptions = { twelve, zone }
  return { display, device, zone, twelve, language: i18n.language, timeOptions }
}
