import { clock, type TimeOptions } from './time'

/**
 * Dates, times and numbers in the format of the chosen language (TIME-3 AC-10): 20.11.2026 and 2,4 MB in German,
 * Nov 20, 2026 and 2.4 MB in English. An added language gets its own format from the browser (Intl).
 */

/** A day: "20.11.2026" in German, "Nov 20, 2026" in English; in `zone` when given. */
export function day(value: string | number | Date, lang: string, zone = ''): string {
  const date = new Date(value)
  const where = zone ? { timeZone: zone } : {}
  if (lang.startsWith('de')) return date.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', ...where })
  return date.toLocaleDateString(lang, { day: 'numeric', month: 'short', year: 'numeric', ...where })
}

/** A day with the time: "20.11.2026, 08:14" or "Nov 20, 2026, 8:14 AM"; with the account's clock and zone. */
export function dayTime(value: string | number | Date, lang: string, options: TimeOptions = {}): string {
  const date = new Date(value)
  return `${day(date, lang, options.zone)}, ${clock(date, lang, options)}`
}

/** A number as the language writes it: 1.234,5 or 1,234.5. */
export function number(value: number, lang: string, digits = 0): string {
  return new Intl.NumberFormat(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)
}

const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'] as const

/** A size: "2,4 MB" or "2.4 MB"; one decimal from MB on below 10, none otherwise ("14 MB", TIME-1 AC-8). */
export function bytes(value: number, lang: string): string {
  let size = Math.max(0, value)
  let unit = 0
  while (size >= 1024 && unit < UNITS.length - 1) {
    size /= 1024
    unit++
  }
  const digits = unit >= 2 && size < 10 ? 1 : 0
  return `${number(size, lang, digits)} ${UNITS[unit]}`
}
