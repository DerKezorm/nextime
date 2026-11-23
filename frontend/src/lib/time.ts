/** "5 minutes ago", "yesterday" in the language of the app. */
export function ago(time: number, lang: string): string {
  const seconds = Math.round((time - Date.now()) / 1000)
  const format = new Intl.RelativeTimeFormat(lang, { numeric: 'auto' })
  const abs = Math.abs(seconds)
  if (abs < 60) return format.format(0, 'minute')
  if (abs < 3600) return format.format(Math.round(seconds / 60), 'minute')
  if (abs < 86400) return format.format(Math.round(seconds / 3600), 'hour')
  if (abs < 86400 * 30) return format.format(Math.round(seconds / 86400), 'day')
  return new Date(time).toLocaleDateString(lang)
}

/** How the account wants times (TIME-3 AC-6, AC-8): 12 or 24 hours, and the zone they stand in. Left out, the
 * language and the device decide. */
export type TimeOptions = { twelve?: boolean; zone?: string }

function timeParts(options: TimeOptions): Intl.DateTimeFormatOptions {
  const fixed = options.twelve === undefined ? {} : { hourCycle: options.twelve ? ('h12' as const) : ('h23' as const) }
  return { hour: options.twelve ? 'numeric' : '2-digit', minute: '2-digit', ...fixed, ...(options.zone ? { timeZone: options.zone } : {}) }
}

/** A moment as the family writes it everywhere: the day as the lists show days (4.10.2026), with hours and minutes, no
 * seconds. The log adds seconds. */
export function moment(value: string | number | Date, lang: string, seconds = false, options: TimeOptions = {}): string {
  return new Date(value).toLocaleString(lang, {
    day: 'numeric',
    month: 'numeric',
    year: 'numeric',
    ...timeParts(options),
    ...(seconds ? { second: '2-digit' } : {}),
  })
}

/** Only the time of day, as hours and minutes ("22:35", "10:35 PM"). */
export function clock(value: string | number | Date, lang: string, options: TimeOptions = {}): string {
  return new Date(value).toLocaleTimeString(lang, timeParts(options))
}
