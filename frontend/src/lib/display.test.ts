/** TIME-3: week numbers after ISO, the week's first day, 12 and 24 hours, zones with half hours and summer time, the
 * hint while travelling, and the night the clocks change. */
import { addDays, clockChange, formatTime, isoWeek, monthWeeks, startOfWeek, travelHint, uses12Hours, utcOffset, weekNumber, weekdayNames, zonePlace } from './display'
import { DEFAULT_DISPLAY } from './display'
import { bytes, day } from './format'

const at = (iso: string) => new Date(iso)

describe('TIME-3 week numbers and the first day of the week', () => {
  it('TIME-3 EC-13 counts weeks after ISO across the turn of the year 2026/2027', () => {
    expect(isoWeek({ year: 2026, month: 11, day: 26 })).toBe(48)
    expect(isoWeek({ year: 2026, month: 12, day: 31 })).toBe(53)
    expect(isoWeek({ year: 2027, month: 1, day: 3 })).toBe(53)
    expect(isoWeek({ year: 2027, month: 1, day: 4 })).toBe(1)
    expect(isoWeek({ year: 2026, month: 1, day: 1 })).toBe(1)
  })

  it('TIME-3 AC-2 a week starts on the day chosen, Monday from the start', () => {
    const thursday = { year: 2026, month: 11, day: 26 }
    expect(DEFAULT_DISPLAY.week_start).toBe(1)
    expect(startOfWeek(thursday, 1)).toEqual({ year: 2026, month: 11, day: 23 })
    expect(startOfWeek(thursday, 0)).toEqual({ year: 2026, month: 11, day: 22 })
    expect(startOfWeek(thursday, 6)).toEqual({ year: 2026, month: 11, day: 21 })
    expect(weekdayNames(0, 'en')[0]).toBe('Sun')
    expect(weekdayNames(6, 'de', 'long')[0]).toBe('Samstag')
  })

  it('TIME-3 EC-13 a week that starts on Sunday or Saturday shows the number of the week its Thursday lies in', () => {
    // Sunday 27 Dec 2026 to Saturday 2 Jan 2027: its Thursday is 31 Dec, ISO week 53.
    expect(weekNumber({ year: 2026, month: 12, day: 27 })).toBe(53)
    // Saturday 2 Jan to Friday 8 Jan 2027: its Thursday is 7 Jan, week 1.
    expect(weekNumber({ year: 2027, month: 1, day: 2 })).toBe(1)
    // Monday start: the ISO week itself.
    expect(weekNumber({ year: 2026, month: 11, day: 23 })).toBe(48)
  })

  it('TIME-3 AC-3 the month grid has whole weeks from the chosen day', () => {
    const weeks = monthWeeks(2026, 11, 1)
    expect(weeks[0][0]).toEqual({ year: 2026, month: 10, day: 26 })
    expect(weeks.at(-1)![6]).toEqual({ year: 2026, month: 12, day: 6 })
    expect(weeks.every((week) => week.length === 7)).toBe(true)
    expect(monthWeeks(2026, 11, 0)[0][0]).toEqual({ year: 2026, month: 11, day: 1 })
    expect(addDays({ year: 2026, month: 12, day: 31 }, 1)).toEqual({ year: 2027, month: 1, day: 1 })
  })
})

describe('TIME-3 times of day', () => {
  it('TIME-3 AC-8 German shows 24 hours, English 12, unless the account fixed one', () => {
    expect(uses12Hours(DEFAULT_DISPLAY, 'de')).toBe(false)
    expect(uses12Hours(DEFAULT_DISPLAY, 'en')).toBe(true)
    expect(uses12Hours({ ...DEFAULT_DISPLAY, clock: '24' }, 'en')).toBe(false)
    expect(uses12Hours({ ...DEFAULT_DISPLAY, clock: '12' }, 'de')).toBe(true)
    const evening = at('2026-11-26T17:30:00Z')
    expect(formatTime(evening, { language: 'de', twelve: false, zone: 'Europe/Berlin' })).toBe('18:30')
    expect(formatTime(evening, { language: 'en', twelve: true, zone: 'Europe/Berlin' })).toBe('6:30 PM')
  })

  it('TIME-3 EC-8 midnight and noon are plain in 12 hours', () => {
    expect(formatTime(at('2026-11-26T23:00:00Z'), { language: 'en', twelve: true, zone: 'Europe/Berlin' })).toBe('12:00 AM')
    expect(formatTime(at('2026-11-26T11:00:00Z'), { language: 'en', twelve: true, zone: 'Europe/Berlin' })).toBe('12:00 PM')
    expect(formatTime(at('2026-11-26T23:00:00Z'), { language: 'de', twelve: false, zone: 'Europe/Berlin' })).toBe('00:00')
  })

  it('TIME-3 EC-9 a zone with half hours and one without summer time show the right time', () => {
    const moment = at('2026-07-01T12:00:00Z')
    expect(formatTime(moment, { language: 'de', twelve: false, zone: 'Asia/Kolkata' })).toBe('17:30')
    expect(formatTime(moment, { language: 'de', twelve: false, zone: 'Asia/Tokyo' })).toBe('21:00')
    expect(utcOffset('Asia/Kolkata', moment)).toBe('UTC+5:30')
    expect(utcOffset('America/New_York', at('2026-01-15T12:00:00Z'))).toBe('UTC−5')
    expect(utcOffset('UTC', moment)).toBe('UTC')
  })

  it('TIME-3 EC-1 the night the clocks change shows the right times and says which hour is skipped or doubled', () => {
    // 29 March 2026: 01:00 UTC is 03:00 in Berlin, a minute before it was 01:59.
    expect(formatTime(at('2026-03-29T00:59:00Z'), { language: 'de', twelve: false, zone: 'Europe/Berlin' })).toBe('01:59')
    expect(formatTime(at('2026-03-29T01:00:00Z'), { language: 'de', twelve: false, zone: 'Europe/Berlin' })).toBe('03:00')
    expect(clockChange({ year: 2026, month: 3, day: 29 }, 'Europe/Berlin')).toEqual({ from: '02:00', to: '03:00' })
    expect(clockChange({ year: 2026, month: 10, day: 25 }, 'Europe/Berlin')).toEqual({ from: '03:00', to: '02:00' })
    expect(clockChange({ year: 2026, month: 3, day: 28 }, 'Europe/Berlin')).toBeNull()
    expect(clockChange({ year: 2026, month: 3, day: 29 }, 'Asia/Kolkata')).toBeNull()
  })
})

describe('TIME-3 the zone while travelling', () => {
  const home = { ...DEFAULT_DISPLAY, home_zone: 'Europe/Berlin' }

  it('TIME-3 AC-5 AC-11 at home no hint; in New York or Kolkata the zone and its offset', () => {
    const winter = at('2026-01-15T12:00:00Z')
    expect(travelHint(home, 'Europe/Berlin', winter)).toBeNull()
    expect(travelHint(home, 'America/New_York', winter)).toEqual({ place: 'New York', offset: 'UTC−5' })
    expect(travelHint(home, 'Asia/Kolkata', winter)).toEqual({ place: 'Kolkata', offset: 'UTC+5:30' })
    // No home zone known yet: no hint.
    expect(travelHint(DEFAULT_DISPLAY, 'America/New_York', winter)).toBeNull()
  })

  it('TIME-3 AC-6 a fixed zone counts instead of the device', () => {
    const fixed = { ...home, time_zone: 'America/New_York' }
    expect(travelHint(fixed, 'Europe/Berlin', at('2026-01-15T12:00:00Z'))).toEqual({ place: 'New York', offset: 'UTC−5' })
    expect(travelHint({ ...home, time_zone: 'Europe/Berlin' }, 'America/New_York')).toBeNull()
    expect(zonePlace('America/Argentina/Buenos_Aires')).toBe('Buenos Aires')
  })
})

describe('TIME-3 AC-10 dates and numbers', () => {
  it('TIME-3 AC-10 write days and sizes as each language does', () => {
    const moment = at('2026-11-26T12:00:00Z')
    expect(day(moment, 'de')).toBe('26.11.2026')
    expect(day(moment, 'en')).toBe('Nov 26, 2026')
    expect(bytes(2.4 * 1024 * 1024, 'de')).toBe('2,4 MB')
    expect(bytes(2.4 * 1024 * 1024, 'en')).toBe('2.4 MB')
  })
})
