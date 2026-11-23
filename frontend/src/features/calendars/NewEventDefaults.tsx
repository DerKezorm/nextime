/**
 * "Neue Termine in diesem Kalender" (TIME-4, decided while testing, 2026-11-23): whole day, length, repeat, reminders and
 * busy that a new event of the calendar starts with, for the person choosing. In "Neuer Kalender" and "Kalender
 * bearbeiten", and on the calendar's page for a calendar shared with the person. Built from nexnotes' pieces: a legend in
 * capitals, fields, chips for the reminders.
 */
import { useId } from 'react'

import { CheckLine } from '../../components/CheckLine'
import { useTranslation } from 'react-i18next'

import { ReminderChips } from '../reminders/ReminderChips'
import { DURATIONS, remindersFor, RULE_CHOICES, USUAL_MINUTES, type NewDefaults, type RuleChoice } from './defaults'

const ALL_DAY_PRESETS = [360, -540]
const PRESETS = [5, 15, 30, 60, 1440]

export function NewEventDefaults({ value, fallbackReminders, onChange, disabled = false }: {
  value: NewDefaults
  /** What new events get as reminders while none were chosen here (the calendar's reminder, TIME-7 AC-3). */
  fallbackReminders: (allDay: boolean) => number[]
  onChange: (value: NewDefaults) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const ids = { length: useId(), rule: useId() }
  const allDay = value.all_day ?? false
  const busy = value.busy ?? !allDay
  const set = (change: NewDefaults) => onChange({ ...value, ...change })
  const switchRow = (label: string, checked: boolean, change: (on: boolean) => void, testId: string) => (
    <CheckLine label={label} checked={checked} onChange={change} disabled={disabled} testId={testId} />
  )
  return (
    <fieldset className="space-y-3" data-testid="new-event-defaults">
      <legend className="nt-legend mb-2">{t('calendars.defaults.title')}</legend>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {switchRow(
          t('panel.allDay'),
          allDay,
          (on) =>
            // Each kind keeps its own reminders: switching the default kind moves nothing between them.
            set({ ...withReminders(value, {}), all_day: on }),
          'defaults-all-day',
        )}
        {switchRow(t('panel.busy'), busy, (on) => set({ busy: on }), 'defaults-busy')}
      </div>
      {/* The length only for events with a time; at a whole day it goes and the repeat takes the row. */}
      <div className={'grid gap-3 ' + (allDay ? '' : 'sm:grid-cols-2')} data-testid="defaults-fields">
        {!allDay && (
          <label className="block text-sm" htmlFor={ids.length}>
            <span className="font-medium text-mist-100">{t('calendars.defaults.length')}</span>
            <select id={ids.length} className="nt-field mt-1" disabled={disabled} value={value.minutes ?? USUAL_MINUTES} onChange={(event) => set({ minutes: Number(event.target.value) })} data-testid="defaults-length">
              {DURATIONS.map((minutes) => (
                <option key={minutes} value={minutes}>
                  {t(`calendars.defaults.minutes_${minutes}`)}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="block text-sm" htmlFor={ids.rule}>
          <span className="font-medium text-mist-100">{t('panel.repeat')}</span>
          <select id={ids.rule} className="nt-field mt-1" disabled={disabled} value={value.rule ?? ''} onChange={(event) => set({ rule: event.target.value as RuleChoice })} data-testid="defaults-rule">
            {RULE_CHOICES.map((choice) => (
              <option key={choice || 'none'} value={choice}>
                {t(`calendars.defaults.rules.${choice || 'none'}`)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {/* Two fields, one per kind (decided after the review, 2026-11-23): a whole day is reminded of the day before
          or on the day, an event with a time some minutes before. */}
      <div className="grid gap-3 sm:grid-cols-2">
        <div data-testid="defaults-reminders-timed">
          <ReminderChips
            label={t('calendars.defaults.remindersTimed')}
            minutes={remindersFor(value, false) ?? fallbackReminders(false)}
            presets={PRESETS}
            allDay={false}
            disabled={disabled}
            onChange={(reminders) => set(withReminders(value, { reminders }))}
          />
        </div>
        <div data-testid="defaults-reminders-all-day">
          <ReminderChips
            label={t('calendars.defaults.remindersAllDay')}
            minutes={remindersFor(value, true) ?? fallbackReminders(true)}
            presets={ALL_DAY_PRESETS}
            allDay
            disabled={disabled}
            onChange={(reminders_all_day) => set(withReminders(value, { reminders_all_day }))}
          />
        </div>
      </div>
    </fieldset>
  )
}

/** The defaults in the form with a field per kind: reminders stored before for whole days move to their own field. */
function withReminders(value: NewDefaults, change: Pick<NewDefaults, 'reminders' | 'reminders_all_day'>): NewDefaults {
  const next: NewDefaults = { ...value, reminders: remindersFor(value, false), reminders_all_day: remindersFor(value, true), ...change }
  if (next.reminders === undefined) delete next.reminders
  if (next.reminders_all_day === undefined) delete next.reminders_all_day
  return next
}
