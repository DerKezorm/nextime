/** Where a profile picture is (TIME-2 AC-5), the letter in its place, and the color of the letter's circle. */
export function avatarUrl(accountId: number, stamp: string | null | undefined): string | null {
  return stamp ? `/api/avatars/${accountId}?v=${encodeURIComponent(stamp)}` : null
}

export function initialOf(name: string): string {
  return (Array.from(name.trim())[0] ?? '?').toUpperCase()
}

/** The calendar colors of the design system, darker tone: white letters stand out on each, in light and dark. */
export const PERSON_COLORS = ['#4a6491', '#c62f47', '#c2410c', '#a16207', '#2f7d4f', '#0e7f74', '#2563a8', '#6d4bc2', '#be3d78', '#8a5a3b', '#61616f'] as const

/** A color of its own for each person, the same on every device: the server gives each account the color fewest
 * others have, so up to eleven people never share one and two with one initial still differ (TIME-2 EC-11). */
export function personColor(index: number): string {
  return PERSON_COLORS[((index % PERSON_COLORS.length) + PERSON_COLORS.length) % PERSON_COLORS.length]
}
