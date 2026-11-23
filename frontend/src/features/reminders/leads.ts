/** Lead times of reminders (TIME-7): minutes before the start, each once, the earliest first. */

/** Four weeks: the longest lead time the server takes. */
export const MAX_MINUTES = 4 * 7 * 1440

export function sortLeads(values: number[]): number[] {
  return [...new Set(values)].sort((a, b) => b - a)
}

/** The note below the reminders, with the sentence that single events of the series keep their own (review of
 * TIME-7, finding 5). */
export function withOccurrences(note: string | undefined, info: { occurrences?: string[] } | undefined, t: (key: string) => string): string | undefined {
  const kept = info?.occurrences?.length ? t('reminders.occurrencesKept') : ''
  return [note, kept].filter(Boolean).join(' ') || undefined
}
