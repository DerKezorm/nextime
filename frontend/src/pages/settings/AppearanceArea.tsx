/**
 * My account › Appearance (plan "Einstellungen", "Darstellung"): first "Aussehen" of the shared account module (light,
 * dark or as the system, kept at the account on every device, E5), then nextime's own cards, one per choice, each saved
 * at once and kept at the account on every device (TIME-3 AC-2, AC-3, AC-6, AC-7; TIME-14 AC-4; the monthly view):
 * first day of the week, week numbers, clock, the same event in two calendars, events in the month. The time zone
 * stands in the profile under the language (E14). Built only from the ui kit.
 */
import { CalendarDays, Clock, Copy, Hash, LayoutGrid } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi, type Display } from '../../api/client'
import { weekdayNames } from '../../lib/display'
import { problemText } from '../../lib/errors'
import { applyAccountTheme } from '../../lib/theme'
import { useDisplay } from '../../lib/useDisplay'
import { useAuth } from '../../state/auth'
import { AppearanceCard } from '../../vendor/nexkonten/AccountCards'
import { Card, CheckboxRow, Notice, Segment, SelectField } from '../../vendor/nexui'

export function AppearanceArea() {
  const { t } = useTranslation()
  const { me, setMe } = useAuth()
  const { display, language } = useDisplay()
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!me) return null
  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    setProblem(null)
    try {
      await work()
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      setBusy(false)
    }
  }
  const save = (change: Partial<Omit<Display, 'home_zone'>>) => void run(async () => setMe(await authApi.display(change)))
  // Monday first, as the choice reads in a calendar; every day can begin the week (AC-2).
  const days = weekdayNames(1, language, 'long').map((name, index) => ({ value: String((index + 1) % 7), label: name }))
  return (
    <>
      <AppearanceCard
        theme={me.theme}
        disabled={busy}
        onChange={(theme) =>
          void run(async () => {
            applyAccountTheme(theme)
            setMe(await authApi.theme(theme))
          })
        }
      />
      <div className="flex flex-col gap-6" data-testid="display-fields">
        <Card id="week-start" icon={CalendarDays} title={t('display.weekStart')}>
          <SelectField
            label={t('display.weekStart')}
            value={String(display.week_start)}
            disabled={busy}
            id="display-week-start"
            data-testid="display-week-start"
            options={days}
            onChange={(event) => save({ week_start: Number(event.target.value) })}
          />
        </Card>
        <Card id="week-numbers" icon={Hash} title={t('display.weekNumbers')}>
          <CheckboxRow label={t('display.weekNumbersShow')} checked={display.week_numbers} disabled={busy} onChange={(on) => save({ week_numbers: on })} testId="display-week-numbers" />
        </Card>
        <Card id="clock" icon={Clock} title={t('display.clock')}>
          <Segment<Display['clock']>
            label={t('display.clock')}
            hideLabel
            value={display.clock}
            disabled={busy}
            onChange={(clock) => save({ clock })}
            options={[
              { value: 'auto', label: t('display.clockAuto'), testId: 'display-clock-auto' },
              { value: '24', label: t('display.clock24'), testId: 'display-clock-24' },
              { value: '12', label: t('display.clock12'), testId: 'display-clock-12' },
            ]}
            testId="display-clock"
          />
        </Card>
        <Card id="duplicates" icon={Copy} title={t('display.duplicates')} subtitle={t('display.duplicatesHint')}>
          <Segment<'once' | 'each'>
            label={t('display.duplicates')}
            hideLabel
            value={display.merge_duplicates === false ? 'each' : 'once'}
            disabled={busy}
            onChange={(choice) => save({ merge_duplicates: choice === 'once' })}
            options={[
              { value: 'once', label: t('display.duplicatesOnce'), testId: 'display-duplicates-once' },
              { value: 'each', label: t('display.duplicatesEach'), testId: 'display-duplicates-each' },
            ]}
            testId="display-duplicates"
          />
        </Card>
        <Card id="month-style" icon={LayoutGrid} title={t('display.monthStyle')}>
          <Segment<'dot' | 'tint'>
            label={t('display.monthStyle')}
            hideLabel
            value={display.month_style === 'tint' ? 'tint' : 'dot'}
            disabled={busy}
            onChange={(month_style) => save({ month_style })}
            options={[
              { value: 'dot', label: t('display.monthStyleDot'), testId: 'display-month-style-dot' },
              { value: 'tint', label: t('display.monthStyleTint'), testId: 'display-month-style-tint' },
            ]}
            testId="display-month-style"
          />
        </Card>
      </div>
      {problem && <Notice tone="danger">{problem}</Notice>}
    </>
  )
}
