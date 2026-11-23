/** Words for values the calendar pages show. */
import { clockText } from './time'

/** "None", "15 min before", "1 day before": the reminder for new events of a calendar (TIME-4 AC-7). */
export function reminderLabel(minutes: number, t: (key: string, values?: Record<string, unknown>) => string): string {
  if (minutes === 0) return t('calendars.reminderNone')
  if (minutes % 1440 === 0) return t('calendars.reminderDays', { count: minutes / 1440 })
  if (minutes % 60 === 0) return t('calendars.reminderHours', { count: minutes / 60 })
  return t('calendars.reminderMinutes', { count: minutes })
}

/** A reminder of an event (TIME-7 AC-2): "15 Min vorher", "zur Startzeit"; at a whole day "am Vortag um 18:00",
 * "am Tag um 09:00" (minutes before the day's midnight), in the member's clock. */
export function leadLabel(minutes: number, allDay: boolean, t: (key: string, values?: Record<string, unknown>) => string, language: string, twelve: boolean): string {
  if (allDay) {
    const daysBefore = Math.max(0, Math.ceil(minutes / 1440))
    const clock = daysBefore * 1440 - minutes
    const time = clockText(`${String(Math.floor(clock / 60)).padStart(2, '0')}:${String(clock % 60).padStart(2, '0')}`, language, twelve)
    if (daysBefore === 0) return t('reminders.onTheDay', { time })
    if (daysBefore === 1) return t('reminders.dayBefore', { time })
    return t('reminders.daysBefore', { count: daysBefore, time })
  }
  if (minutes === 0) return t('reminders.atStart')
  return reminderLabel(minutes, t)
}
