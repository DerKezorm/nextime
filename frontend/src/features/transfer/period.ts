/** The period a file of TIME-24 spans, as the preview names it (AC-2). */

/** "Januar bis Dezember 2025", "März 2024 bis Mai 2026", "14. Oktober 2025" (`between`: " bis ", " to "). */
export function periodText(first: string | null, last: string | null, language: string, between = ' – '): string {
  if (!first || !last) return ''
  const at = (day: string) => new Date(`${day}T12:00:00Z`)
  const month = (day: string, year: boolean) => new Intl.DateTimeFormat(language, { month: 'long', ...(year ? { year: 'numeric' } : {}), timeZone: 'UTC' }).format(at(day))
  if (first === last) return new Intl.DateTimeFormat(language, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(at(first))
  if (first.slice(0, 7) === last.slice(0, 7)) return month(first, true)
  const sameYear = first.slice(0, 4) === last.slice(0, 4)
  return `${month(first, !sameYear)}${between}${month(last, true)}`
}
