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

/** The room between months and between fields, and what a month needs above its fields (name and weekdays). */
const GAP_X = 20
const GAP_Y = 16
const FIELD_GAP = 3
const MONTH_HEAD = 50
/** Fields never larger than this (a wide screen), and smaller ones than this only on a phone, where it may scroll. */
const FIELD_MAX = 48
const FIELD_MIN = 14

/**
 * How the twelve months stand in `width` × `height` pixels: 2, 3, 4 or 6 side by side, whichever gives the largest
 * field that still lets the whole year fit without scrolling (found while testing: four months side by side made each
 * field 50 px, and the year ran off the bottom); more side by side while the fields keep four fifths of the largest size,
 * so a wide view is filled. Where nothing fits (a phone), two side by side, scrolling.
 */
export function yearFit(width: number, height: number): { columns: number; month: number } {
  const choices = [2, 3, 4, 6].map((columns) => {
    const rows = 12 / columns
    const byWidth = ((width - (columns - 1) * GAP_X) / columns - 6 * FIELD_GAP) / 7
    const byHeight = ((height - (rows - 1) * GAP_Y) / rows - MONTH_HEAD - 5 * FIELD_GAP) / 6
    return { columns, field: Math.min(byWidth, byHeight, FIELD_MAX) }
  })
  const largest = Math.max(...choices.map((choice) => choice.field))
  // More months side by side fill a wide view; worth fields a little smaller (found while testing: with the column
  // folded away the room on the right stayed empty).
  const best = [...choices].reverse().find((choice) => choice.field >= largest * 0.8) ?? choices[0]
  if (best.field < FIELD_MIN) {
    const field = Math.min(((width - GAP_X) / 2 - 6 * FIELD_GAP) / 7, FIELD_MAX)
    return { columns: 2, month: Math.floor(7 * field + 6 * FIELD_GAP) }
  }
  return { columns: best.columns, month: Math.floor(7 * best.field + 6 * FIELD_GAP) }
}
