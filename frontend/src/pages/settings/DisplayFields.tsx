/**
 * The choices of "Appearance" that belong to the account and travel to every device (TIME-3 AC-2, AC-3, AC-6, AC-7,
 * AC-8, EC-7): first day of the week, week numbers, clock and time zone; and whether the same event in two calendars
 * shows once (TIME-14 AC-4). Each choice is saved at once; light or dark
 * stays on this device and stands above them (`ThemeSwitcher`).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi, type Display } from '../../api/client'
import { Problem } from '../../components/Field'
import { knownZones, weekdayNames, zonePlace } from '../../lib/display'
import { problemText } from '../../lib/errors'
import { useDisplay } from '../../lib/useDisplay'
import { useAuth } from '../../state/auth'

function Row({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="grid items-center gap-1 text-sm sm:grid-cols-[minmax(0,11rem)_1fr] sm:gap-3">
      <label htmlFor={id} className="font-medium">
        {label}
      </label>
      {children}
    </div>
  )
}

export function DisplayFields() {
  const { t } = useTranslation()
  const { setMe } = useAuth()
  const { display, device, language } = useDisplay()
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const save = async (change: Partial<Omit<Display, 'home_zone'>>) => {
    setBusy(true)
    setProblem(null)
    try {
      setMe(await authApi.display(change))
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      setBusy(false)
    }
  }
  // Monday first, as the choice reads in a calendar; every day can begin the week (AC-2).
  const days = weekdayNames(1, language, 'long').map((name, index) => ({ value: (index + 1) % 7, name }))
  const zones = knownZones()
  const deviceName = zonePlace(device || 'UTC')
  return (
    <div className="space-y-3" data-testid="display-fields">
      <Row id="display-week-start" label={t('display.weekStart')}>
        <select id="display-week-start" className="nt-field" disabled={busy} value={display.week_start} onChange={(event) => void save({ week_start: Number(event.target.value) })}>
          {days.map((day) => (
            <option key={day.value} value={day.value}>
              {day.name}
            </option>
          ))}
        </select>
      </Row>
      <Row id="display-week-numbers" label={t('display.weekNumbers')}>
        <select id="display-week-numbers" className="nt-field" disabled={busy} value={display.week_numbers ? 'on' : 'off'} onChange={(event) => void save({ week_numbers: event.target.value === 'on' })}>
          <option value="on">{t('display.show')}</option>
          <option value="off">{t('display.hide')}</option>
        </select>
      </Row>
      <Row id="display-clock" label={t('display.clock')}>
        <select id="display-clock" className="nt-field" disabled={busy} value={display.clock} onChange={(event) => void save({ clock: event.target.value as Display['clock'] })}>
          <option value="auto">{t('display.clockAuto')}</option>
          <option value="24">{t('display.clock24')}</option>
          <option value="12">{t('display.clock12')}</option>
        </select>
      </Row>
      <Row id="display-zone" label={t('display.zone')}>
        <select id="display-zone" className="nt-field" disabled={busy} value={display.time_zone} onChange={(event) => void save({ time_zone: event.target.value })}>
          <option value="">{t('display.zoneDevice', { place: deviceName })}</option>
          {zones.map((zone) => (
            <option key={zone} value={zone}>
              {zone.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </Row>
      <p className="text-xs leading-relaxed text-mist-500">{t('display.zoneHint')}</p>
      <Row id="display-duplicates" label={t('display.duplicates')}>
        <select id="display-duplicates" className="nt-field" disabled={busy} value={display.merge_duplicates === false ? 'each' : 'once'} onChange={(event) => void save({ merge_duplicates: event.target.value === 'once' })}>
          <option value="once">{t('display.duplicatesOnce')}</option>
          <option value="each">{t('display.duplicatesEach')}</option>
        </select>
      </Row>
      <p className="text-xs leading-relaxed text-mist-500">{t('display.duplicatesHint')}</p>
      <Problem text={problem} />
    </div>
  )
}
