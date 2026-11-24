/**
 * Wall-clock times as the side panel edits them (`YYYY-MM-DD`, `HH:MM`) and the instants the server lists. The zone
 * arithmetic goes through `Intl`, so the change of the clocks comes out right without tables of our own.
 */

/** One formatter per zone: making one is far dearer than using it, and a month asks thousands of times (TIME-12 EC-9). */
const offsetFormats = new Map<string, Intl.DateTimeFormat>()

function offsetFormat(zone: string): Intl.DateTimeFormat {
  let found = offsetFormats.get(zone)
  if (!found) {
    found = new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: 'longOffset' })
    offsetFormats.set(zone, found)
  }
  return found
}

/** The offset of `zone` at the instant `ms`, in minutes east of UTC. */
export function offsetMinutes(zone: string, ms: number): number {
  try {
    const name = offsetFormat(zone).formatToParts(new Date(ms)).find((part) => part.type === 'timeZoneName')?.value ?? 'GMT'
    const found = /GMT([+-])(\d{1,2})(?::?(\d{2}))?/.exec(name)
    return found ? (found[1] === '-' ? -1 : 1) * (Number(found[2]) * 60 + Number(found[3] ?? 0)) : 0
  } catch {
    return 0
  }
}

/** A date that exists (no 31 February): `YYYY-MM-DD`. */
export function validDate(text: string): boolean {
  const found = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (!found) return false
  const [year, month, day] = [Number(found[1]), Number(found[2]), Number(found[3])]
  const value = new Date(Date.UTC(year, month - 1, day))
  return value.getUTCFullYear() === year && value.getUTCMonth() === month - 1 && value.getUTCDate() === day
}

export function validTime(text: string): boolean {
  const found = /^(\d{2}):(\d{2})$/.exec(text)
  return !!found && Number(found[1]) < 24 && Number(found[2]) < 60
}

/** The instant of a wall-clock time in a zone. */
export function instantOf(date: string, time: string, zone: string): number {
  const [year, month, day] = date.split('-').map(Number)
  const [hour, minute] = time.split(':').map(Number)
  const guess = Date.UTC(year, month - 1, day, hour, minute)
  let ms = guess - offsetMinutes(zone, guess) * 60_000
  ms = guess - offsetMinutes(zone, ms) * 60_000
  return ms
}

/** The wall clock of an instant in a zone: date and time. */
export function wallOf(ms: number, zone: string): { date: string; time: string } {
  const shifted = new Date(ms + offsetMinutes(zone, ms) * 60_000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return {
    date: `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`,
    time: `${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`,
  }
}

/** A day plus some days, as `YYYY-MM-DD`. */
export function addDays(date: string, days: number): string {
  const [year, month, day] = date.split('-').map(Number)
  const value = new Date(Date.UTC(year, month - 1, day + days))
  return value.toISOString().slice(0, 10)
}

/** The start of a new event: the next full hour in the zone shown (TIME-5 AC-10 then adds an hour). */
export function nextFullHour(zone: string, now: number = Date.now()): { date: string; time: string } {
  return wallOf(Math.ceil((now + 1) / 3_600_000) * 3_600_000, zone)
}

/** "Do, 26. November" or "Thu, November 26": a day as a sentence names it. */
export function dayName(date: string, language: string): string {
  const [year, month, day] = date.split('-').map(Number)
  return new Intl.DateTimeFormat(language, { weekday: 'short', day: 'numeric', month: 'long', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, day)))
}

/** "26.11.2027" or "11/26/2027". */
export function shortDate(date: string, language: string): string {
  const [year, month, day] = date.split('-').map(Number)
  return new Intl.DateTimeFormat(language, { day: 'numeric', month: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, day)))
}

/** A wall time `HH:MM` in the account's clock: "18:30" or "6:30 PM". */
export function clockText(time: string, language: string, twelve: boolean): string {
  const [hour, minute] = time.split(':').map(Number)
  return new Intl.DateTimeFormat(language, { hour: twelve ? 'numeric' : '2-digit', minute: '2-digit', hourCycle: twelve ? 'h12' : 'h23', timeZone: 'UTC' }).format(
    new Date(Date.UTC(2000, 0, 1, hour, minute)),
  )
}
