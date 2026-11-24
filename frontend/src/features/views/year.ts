/** The steps and the paging of the year as a heat map (TIME-12 AC-16, AC-19). */

export type HeatStep = 0 | 1 | 2 | 3 | 4

/** The step of a day with `count` events: free, 1, 2–3, 4–5, 6 and more (AC-19). */
export function heatStep(count: number): HeatStep {
  if (count <= 0) return 0
  if (count === 1) return 1
  if (count <= 3) return 2
  if (count <= 5) return 3
  return 4
}

/** The same day `years` later; the 29th of February becomes the 28th in a year without it. */
export function addYears(date: string, years: number): string {
  const year = Number(date.slice(0, 4)) + years
  const rest = date.slice(4)
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
  return `${String(year).padStart(4, '0')}${rest === '-02-29' && !leap ? '-02-28' : rest}`
}
