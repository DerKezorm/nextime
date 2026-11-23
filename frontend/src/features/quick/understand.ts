/**
 * "Schnell eintragen" (TIME-10): what a sentence means, in German and English alike (AC-3), without any service of
 * others. A pure function of the text, the clock and zone of the device (EC-13) and the account's calendars, so the
 * preview stands there while typing and the tests set the clock.
 *
 * Understood: a calendar (`#Familie`, the longest name that fits, the upper one of two alike, EC-8; one that does not
 * fit or may only be read stays in the title, EC-7), a repetition ("jeden Montag", "every Monday", "täglich",
 * "werktags", "alle 2 Wochen"), a length ("2 Std", "90 min", "2 hours"), an end ("bis 17 Uhr", "until 5 pm",
 * "15–17 Uhr"), a date (heute, morgen, übermorgen, weekdays, "in 3 Tagen", 27.11., "3. März", "March 3",
 * 2026-11-27) and a time ("15 Uhr", "15:30", "um 15", "3 pm"). The fixed rules of EC-2 decide what is left open. What
 * is not understood stays in the title (the place too: TIME-10 has none).
 */
import type { Rule, Weekday } from '../calendars/api'
import { weekdayOf, WEEKDAYS } from '../calendars/rule'
import { addDays, instantOf, validDate, wallOf } from '../calendars/time'

export const TITLE_MAX = 200
/** Without a length or an end an event lasts an hour (AC-4). */
export const DEFAULT_MINUTES = 60

export type QuickCalendar = { id: number; name: string; writable: boolean }

export type Context = {
  /** Now, on the device's clock (ms). */
  now: number
  /** The zone the times mean (the device's or the one held, TIME-3). */
  zone: string
  /** The account's calendars in the order of its list. */
  calendars: QuickCalendar[]
}

export type Understood =
  | { kind: 'empty' }
  /** No date (EC-1), or an impossible one ("31. Februar", EC-13). */
  | { kind: 'noDate'; text: string }
  | {
      kind: 'event'
      title: string
      allDay: boolean
      /** `YYYY-MM-DD`, and `HH:MM` for a time. */
      date: string
      start: string | null
      endDate: string
      end: string | null
      /** The length came from nowhere: one hour (AC-4). */
      defaultLength: boolean
      rule: Rule | null
      /** The calendar `#Name` named; `null` when none was named or it did not fit. */
      calendarId: number | null
      /** `#Name` that fits no calendar one may write into, as typed (EC-7). */
      tag: { text: string; problem: 'unknown' | 'readOnly' } | null
      problem: null | 'end' | 'title' | 'long' | 'noTime'
    }

const DAY_NAMES: Record<string, Weekday> = {
  montag: 'MO', monday: 'MO', dienstag: 'TU', tuesday: 'TU', mittwoch: 'WE', wednesday: 'WE', donnerstag: 'TH',
  thursday: 'TH', freitag: 'FR', friday: 'FR', samstag: 'SA', sonnabend: 'SA', saturday: 'SA', sonntag: 'SU',
  sunday: 'SU',
}
const DAY_SHORT: Record<string, Weekday> = {
  mo: 'MO', di: 'TU', mi: 'WE', do: 'TH', fr: 'FR', sa: 'SA', so: 'SU', mon: 'MO', tue: 'TU', tues: 'TU', wed: 'WE',
  thu: 'TH', thur: 'TH', thurs: 'TH', fri: 'FR', sat: 'SA', sun: 'SU',
}
/** "montags": every Monday. */
const DAY_EVERY: Record<string, Weekday> = {
  montags: 'MO', dienstags: 'TU', mittwochs: 'WE', donnerstags: 'TH', freitags: 'FR', samstags: 'SA', sonntags: 'SU',
}
const MONTHS: Record<string, number> = {
  januar: 1, jänner: 1, jan: 1, january: 1, februar: 2, feb: 2, february: 2, märz: 3, maerz: 3, mär: 3, march: 3,
  mar: 3, april: 4, apr: 4, mai: 5, may: 5, juni: 6, jun: 6, june: 6, juli: 7, jul: 7, july: 7, august: 8, aug: 8,
  september: 9, sep: 9, sept: 9, oktober: 10, okt: 10, october: 10, oct: 10, november: 11, nov: 11, dezember: 12,
  dez: 12, december: 12, dec: 12,
}

const LONG_DAY = Object.keys(DAY_NAMES).join('|')
const SHORT_DAY = Object.keys(DAY_SHORT).join('|')
const MONTH = Object.keys(MONTHS).join('|')
/** A time of day: "15", "15:30", "15.30", with "Uhr", "am", "pm" ("2h" is a length). */
const TIME = String.raw`(\d{1,2})(?:[:.](\d{2}))?\s*(uhr|am|pm|a\.m\.|p\.m\.)?`
/** What separates two weekdays: "Montag und Donnerstag", "Mo, Do", "Monday and Thursday". */
const AND = String.raw`\s*(?:,|und|and|&|\+)\s*`

function fold(text: string): string {
  return text.normalize('NFC').toLocaleLowerCase()
}

const pad = (value: number) => String(value).padStart(2, '0')

/** A wall time from hours, minutes and a suffix; `null` when it is none ("25 Uhr", "13 pm"). */
function clock(hours: string, minutes: string | undefined, suffix: string | undefined): string | null {
  let hour = Number(hours)
  const minute = Number(minutes ?? 0)
  const kind = (suffix ?? '').toLowerCase().replace(/\./g, '')
  if (kind === 'am' || kind === 'pm') {
    if (hour < 1 || hour > 12) return null
    if (kind === 'pm' && hour < 12) hour += 12
    if (kind === 'am' && hour === 12) hour = 0
  }
  if (hour > 23 || minute > 59) return null
  return `${pad(hour)}:${pad(minute)}`
}

function minutesOf(time: string): number {
  const [hour, minute] = time.split(':').map(Number)
  return hour * 60 + minute
}

/** The weekday of a date as 0 (Monday) to 6. */
function dayIndex(date: string): number {
  return WEEKDAYS.indexOf(weekdayOf(date))
}

/** The next date on or after `from` that falls on `day`. */
function onOrAfter(from: string, day: Weekday): string {
  return addDays(from, (WEEKDAYS.indexOf(day) - dayIndex(from) + 7) % 7)
}

/** A date that exists, or `'invalid'`; `null` when the numbers are no date at all. Without a year: the next one to
 * come (EC-2). */
function dateOf(day: number, month: number, year: string | undefined, today: string): string | 'invalid' {
  let full = year ? Number(year) : Number(today.slice(0, 4))
  if (year && year.length === 2) full = 2000 + full
  const text = `${full}-${pad(month)}-${pad(day)}`
  if (!validDate(text)) {
    // "29. Februar" without a year: the next 29 February to come.
    if (!year && month === 2 && day === 29) {
      for (let next = full; next < full + 8; next++) {
        const leap = `${next}-02-29`
        if (validDate(leap) && leap >= today) return leap
      }
    }
    return 'invalid'
  }
  if (!year && text < today) return `${full + 1}-${pad(month)}-${pad(day)}`
  return text
}

type Repeat = { rule: Rule; days: Weekday[] | null; nth?: { weekday: Weekday; nth: number } }

/** Words that make a sentence German or English: German "am" after a time is a preposition ("18:30 am Bahnhof"),
 * English "am" is a.m. (review of TIME-10, finding 1). */
const GERMAN = /(?:^|\s)(?:uhr|um|morgen|heute|übermorgen|uebermorgen|jeden|jede|jedes|bis|ab|nächsten|naechsten|kommenden|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|täglich|werktags|abends|mittags)(?=\s|$)/iu
const ENGLISH = /(?:^|\s)(?:tomorrow|today|tonight|next|on|at|every|until|monday|tuesday|wednesday|thursday|friday|saturday|sunday|daily|weekly|pm|p\.m\.)(?=\s|$)/iu
/** Put before an "am" that is no a.m.: no pattern of a time takes it any more, the title loses it again. */
const KEEP = '\u2060'

/** The date on or after `from` that is the `nth` (1 to 4, or -1 for the last) `weekday` of its month. */
function nthOnOrAfter(from: string, weekday: Weekday, nth: number): string {
  let day = onOrAfter(from, weekday)
  for (let step = 0; step < 60; step++) {
    const [year, month, date] = day.split('-').map(Number)
    const last = new Date(Date.UTC(year, month, 0)).getUTCDate()
    const which = nth === -1 ? date + 7 > last : Math.floor((date - 1) / 7) + 1 === nth
    if (which) return day
    day = addDays(day, 7)
  }
  return day
}

/** A date written out: `27.11.`, `27.11.2026`, `3. März`, `March 3`, `2026-11-27`; `null` when it is none. */
function writtenDate(text: string, today: string): string | 'invalid' | null {
  let found = /^(\d{4})-(\d{2})-(\d{2})$/u.exec(text)
  if (found) return dateOf(Number(found[3]), Number(found[2]), found[1], today)
  found = /^(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?$/u.exec(text)
  if (found) return dateOf(Number(found[1]), Number(found[2]), found[3], today)
  found = new RegExp(String.raw`^(\d{1,2})(?:\.|st|nd|rd|th)?\s+(?:of\s+)?(${MONTH})\.?(?:\s+(\d{4}))?$`, 'iu').exec(text)
  if (found) return dateOf(Number(found[1]), MONTHS[fold(found[2])], found[3], today)
  found = new RegExp(String.raw`^(${MONTH})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?$`, 'iu').exec(text)
  if (found) return dateOf(Number(found[2]), MONTHS[fold(found[1])], found[3], today)
  return null
}

/** The forms of `writtenDate` as one pattern. */
const DATE = String.raw`(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})?|\d{1,2}(?:\.|st|nd|rd|th)?\s+(?:of\s+)?(?:${MONTH})\.?(?:\s+\d{4})?|(?:${MONTH})\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)`

function weekly(days: Weekday[] | null, interval = 1): Repeat {
  return { rule: { freq: 'weekly', interval, weekdays: days ?? [], monthly: 'date', end: 'never', until: null, count: null }, days }
}

function plain(freq: Rule['freq'], interval = 1): Repeat {
  return { rule: { freq, interval, weekdays: [], monthly: 'date', end: 'never', until: null, count: null }, days: null }
}

export function understand(raw: string, context: Context): Understood {
  // Pasted lines become one (EC-6); blanks at the edges go (EC-5).
  const typed = raw.replace(/\s+/g, ' ').trim()
  if (!typed) return { kind: 'empty' }
  let rest = ` ${typed} `
  const take = (found: RegExpExecArray) => {
    rest = rest.slice(0, found.index) + ' ' + rest.slice(found.index + found[0].length)
  }
  const find = (pattern: string): RegExpExecArray | null => new RegExp(pattern, 'iu').exec(rest)
  const today = wallOf(context.now, context.zone).date
  const nowTime = wallOf(context.now, context.zone).time
  // German "am" after a time is a preposition: in a German sentence, after an hour past 12, or before a capitalised
  // word in a sentence that is not English ("18:30 am Bahnhof", "12:00 am Bahnhof"); attached ("9am") it is a.m.
  const german = GERMAN.test(rest)
  const english = ENGLISH.test(rest)
  rest = rest.replace(/(\d{1,2})((?:[:.]\d{2})?)(\s+)am(?=\s+(\S))/gu, (whole, hours: string, minutes: string, space: string, next: string) =>
    german || Number(hours) > 12 || (/\p{Lu}/u.test(next) && !english) ? `${hours}${minutes}${space}${KEEP}am` : whole,
  )

  // The calendar: "#Familie", the longest name of a calendar one may write into (EC-7, EC-8).
  let calendarId: number | null = null
  let tag: Extract<Understood, { kind: 'event' }>['tag'] = null
  const hash = /\s#(\S+)/u.exec(rest)
  if (hash) {
    const after = fold(rest.slice(hash.index + 2))
    const fits = (entry: QuickCalendar) => {
      const name = fold(entry.name.trim())
      return name.length > 0 && after.startsWith(name) && (after.length === name.length || /[\s,.;:!?)]/u.test(after[name.length]))
    }
    const longest = (entries: QuickCalendar[]) =>
      entries.filter(fits).reduce<QuickCalendar | null>((best, entry) => (!best || entry.name.trim().length > best.name.trim().length ? entry : best), null)
    const chosen = longest(context.calendars.filter((entry) => entry.writable))
    if (chosen) {
      calendarId = chosen.id
      const length = chosen.name.trim().length
      rest = rest.slice(0, hash.index) + ' ' + rest.slice(hash.index + 2 + length)
    } else {
      tag = { text: `#${hash[1]}`, problem: longest(context.calendars) ? 'readOnly' : 'unknown' }
    }
  }

  // The repetition (AC-6).
  let repeat: Repeat | null = null
  let found: RegExpExecArray | null
  const ANY_DAY = `${LONG_DAY}|${SHORT_DAY}`
  const dayOf = (name: string): Weekday => DAY_NAMES[fold(name.trim().replace(/\.$/, ''))] ?? DAY_SHORT[fold(name.trim().replace(/\.$/, ''))]
  if ((found = find(String.raw`\s(?:jeden\s+werktag|werktags|wochentags|every\s+weekday|weekdays)(?=\s)`))) {
    repeat = weekly(['MO', 'TU', 'WE', 'TH', 'FR'])
    take(found)
  } else if ((found = find(String.raw`\s(?:jeden|jede|every|each)\s+(?:([1-5])(?:\.|st|nd|rd|th)?|(letzten|last))\s+(${LONG_DAY})(?=\s)`))) {
    // "jeden 2. Dienstag", "every last Friday": monthly on that weekday (TIME-6 AC-1; a fifth one is the last, EC-4).
    const nth = found[2] || found[1] === '5' ? -1 : Number(found[1])
    repeat = { ...plain('monthly'), nth: { weekday: DAY_NAMES[fold(found[3])], nth } }
    repeat.rule.monthly = nth === -1 ? 'last' : 'nth'
    take(found)
  } else if ((found = find(String.raw`\s(?:jeden|jede|jedes|every|each)\s+((?:${ANY_DAY})\.?(?:${AND}(?:${ANY_DAY})\.?)*)(?=\s)`))) {
    const days = found[1].split(new RegExp(AND, 'iu')).map(dayOf)
    repeat = weekly([...new Set(days)])
    take(found)
  } else if ((found = find(String.raw`\s((?:${ANY_DAY})\.?(?:${AND}(?:${ANY_DAY})\.?)+)(?=\s)`))) {
    // Several weekdays without "jeden" ("Mo, Mi 9 Uhr Kurs"): every week on those days.
    const days = found[1].split(new RegExp(AND, 'iu')).map(dayOf)
    repeat = weekly([...new Set(days)])
    take(found)
  } else if ((found = find(String.raw`\s((?:${Object.keys(DAY_EVERY).join('|')})(?:${AND}(?:${Object.keys(DAY_EVERY).join('|')}))*)(?=\s)`))) {
    const days = found[1].split(new RegExp(AND, 'iu')).map((name) => DAY_EVERY[fold(name.trim())])
    repeat = weekly([...new Set(days)])
    take(found)
  } else if ((found = find(String.raw`\s(?:alle|every)\s+(\d{1,3})\s+(tage|days|wochen|weeks|monate|months|jahre|years)(?=\s)`))) {
    const interval = Math.max(1, Number(found[1]))
    const unit = fold(found[2])
    repeat = unit.startsWith('w') ? weekly(null, interval) : plain(unit === 'tage' || unit === 'days' ? 'daily' : unit.startsWith('mo') ? 'monthly' : 'yearly', interval)
    take(found)
  } else if ((found = find(String.raw`\s(?:jeden\s+tag|täglich|taeglich|daily|every\s+day)(?=\s)`))) {
    repeat = plain('daily')
    take(found)
  } else if ((found = find(String.raw`\s(?:jede\s+woche|wöchentlich|woechentlich|weekly|every\s+week)(?=\s)`))) {
    repeat = weekly(null)
    take(found)
  } else if ((found = find(String.raw`\s(?:jeden\s+monat|monatlich|monthly|every\s+month)(?=\s)`))) {
    repeat = plain('monthly')
    take(found)
  } else if ((found = find(String.raw`\s(?:jedes\s+jahr|jährlich|jaehrlich|yearly|annually|every\s+year)(?=\s)`))) {
    repeat = plain('yearly')
    take(found)
  }

  // The end of a series: "jeden Montag bis 31.12." (review of TIME-10, finding 3).
  let until: string | 'invalid' | null = null
  if (repeat && (found = find(String.raw`\s(?:bis|until|till)\s+(?:zum\s+|am\s+|einschließlich\s+)?(${DATE})(?=\s)`))) {
    until = writtenDate(found[1].trim(), today)
    if (until !== null) take(found)
  }

  // The length (AC-4).
  let length: number | null = null
  if ((found = find(String.raw`\s(?:für\s+|for\s+)?(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:std\.?|stunden?|h|hrs?\.?|hours?)(?=\s)`))) {
    length = Math.round(Number(found[1].replace(',', '.')) * 60)
    take(found)
  } else if ((found = find(String.raw`\s(?:für\s+|for\s+)?(\d{1,4})\s*(?:min\.?|minuten|minute|mins?\.?|minutes?)(?=\s)`))) {
    length = Number(found[1])
    take(found)
  } else if ((found = find(String.raw`\s(?:für\s+|for\s+)?(?:eine\s+halbe\s+stunde|halbe\s+stunde|half\s+an\s+hour)(?=\s)`))) {
    length = 30
    take(found)
  } else if ((found = find(String.raw`\s(?:für\s+|for\s+)?(?:eine\s+stunde|an\s+hour|one\s+hour)(?=\s)`))) {
    length = 60
    take(found)
  }
  if (length !== null && length <= 0) length = null

  // A span "15–17 Uhr", "3-5 pm", "von 9 bis 11 Uhr"; then an end alone "bis 17 Uhr".
  let start: string | null = null
  let end: string | null = null
  if ((found = find(String.raw`\s(?:von\s+|from\s+|um\s+|at\s+)?${TIME}\s*(?:-|–|bis|to|until|till)\s*${TIME}(?=\s)`))) {
    const [, h1, m1, s1, h2, m2, s2] = found
    if (s1 || s2 || m1 || m2) {
      // "3-5 pm": the second's half of the day counts for the first, unless that puts it after the end ("11-1 pm").
      const second = clock(h2, m2, s2 ?? (s1 && /am|pm/i.test(s1) ? s1 : undefined))
      const borrowed = !s1 && s2 && /am|pm/i.test(s2) && Number(h1) <= 12 ? clock(h1, m1, s2) : null
      const first = borrowed && second && minutesOf(borrowed) <= minutesOf(second) ? borrowed : clock(h1, m1, s1)
      if (first && second) {
        start = first
        end = second
        take(found)
      }
    }
  }
  if (end === null && (found = find(String.raw`\s(?:bis|until|till|to)\s+${TIME}(?=\s)`))) {
    const [, hours, minutes, suffix] = found
    const value = minutes || suffix ? clock(hours, minutes, suffix) : null
    if (value) {
      end = value
      take(found)
    }
  }

  // The date.
  let date: string | null = null
  let weekday: Weekday | null = null
  let nextOne = false
  let lastDay: string | null = null
  let lastWeekday: Weekday | null = null
  let invalid = false
  /** "abends", "heute Abend": a time without am/pm before 12 means the afternoon or evening. */
  let evening = false
  const setDate = (value: string | 'invalid') => {
    if (value === 'invalid') invalid = true
    else date = value
  }
  const DAYTIME = String.raw`(?:\s+(abend|nachmittag|vormittag|mittag|morgen|früh|nacht|evening|afternoon|morning))?`
  if ((found = find(String.raw`\s(?:abends|nachmittags)(?=\s)`))) {
    evening = true
    take(found)
  }
  if ((found = find(String.raw`\s(?:vormittags|morgens|nachts)(?=\s)`))) take(found)
  const daytime = (word: string | undefined) => {
    if (word && /^(abend|nachmittag|evening|afternoon)$/iu.test(word)) evening = true
  }
  if ((found = find(String.raw`\s(?:von\s+|from\s+)?(${LONG_DAY})\s*(?:bis|-|–|to|until|till)\s*(${LONG_DAY})(?=\s)`))) {
    weekday = DAY_NAMES[fold(found[1])]
    lastWeekday = DAY_NAMES[fold(found[2])]
    take(found)
  } else if ((found = find(String.raw`\s(?:vom\s+|von\s+|from\s+)?(${DATE})\s*(?:bis|-|–|to|until|till)\s*(?:zum\s+)?(${DATE})(?=\s)`))) {
    const first = writtenDate(found[1].trim(), today)
    const last = writtenDate(found[2].trim(), today)
    if (first && last) {
      setDate(first)
      if (last === 'invalid') invalid = true
      else lastDay = last
      take(found)
    }
  } else if ((found = find(String.raw`\s(?:übermorgen|uebermorgen|day\s+after\s+tomorrow)${DAYTIME}(?=\s)`))) {
    setDate(addDays(today, 2))
    daytime(found[1])
    take(found)
  } else if ((found = find(String.raw`\s(?:heute|today)${DAYTIME}(?=\s)`)) || (found = find(String.raw`\s(tonight)(?=\s)`))) {
    // "heute Morgen" is this morning, not tomorrow.
    setDate(today)
    daytime(found[1] === 'tonight' ? 'evening' : found[1])
    take(found)
  } else if ((found = find(String.raw`\s(?:morgen|tomorrow)${DAYTIME}(?=\s)`))) {
    setDate(addDays(today, 1))
    daytime(found[1])
    take(found)
  } else if ((found = find(String.raw`\s(?:in\s+)(\d{1,3}|einer|einem|a|one)\s+(tagen?|days?|wochen?|weeks?)(?=\s)`))) {
    const count = /^\d+$/.test(found[1]) ? Number(found[1]) : 1
    setDate(addDays(today, count * (/^(tag|day)/i.test(found[2]) ? 1 : 7)))
    take(found)
  } else if ((found = find(String.raw`\s(?:am\s+|on\s+)?(\d{4})-(\d{2})-(\d{2})(?=\s)`))) {
    setDate(dateOf(Number(found[3]), Number(found[2]), found[1], today))
    take(found)
  } else if ((found = find(String.raw`\s(?:am\s+|on\s+)?(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?=\s)`))) {
    setDate(dateOf(Number(found[1]), Number(found[2]), found[3], today))
    take(found)
  } else if ((found = find(String.raw`\s(?:am\s+|on\s+|the\s+)?(\d{1,2})(?:\.|st|nd|rd|th)?\s+(?:of\s+)?(${MONTH})\.?(?:\s+(\d{4}))?(?=\s)`))) {
    setDate(dateOf(Number(found[1]), MONTHS[fold(found[2])], found[3], today))
    take(found)
  } else if ((found = find(String.raw`\s(?:on\s+)?(${MONTH})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?(?:\s+(\d{4}))?(?=\s)`))) {
    setDate(dateOf(Number(found[2]), MONTHS[fold(found[1])], found[3], today))
    take(found)
  } else if ((found = find(String.raw`\s(?:(am|on|nächsten|naechsten|kommenden|next|this|diesen)\s+)?(${LONG_DAY})${DAYTIME}(?=\s)`))) {
    weekday = DAY_NAMES[fold(found[2])]
    // "nächsten Montag" on a Monday is the one in a week (review of TIME-10, finding 2).
    nextOne = !!found[1] && /^(nächsten|naechsten|kommenden|next)$/iu.test(found[1])
    daytime(found[3])
    take(found)
  } else if ((found = find(String.raw`\s(?:am\s+|on\s+)?(${SHORT_DAY})\.?,?(?=\s+\d)`))) {
    // Short names only before a time or a date ("Fr 18 Uhr"): "Do" or "So" alone may be a word of the title.
    weekday = DAY_SHORT[fold(found[1])]
    take(found)
  }

  // The time (AC-7).
  if (start === null) {
    if ((found = find(String.raw`\s(?:um\s+|at\s+|ab\s+|gegen\s+)?(\d{1,2}):(\d{2})\s*(uhr|am|pm|a\.m\.|p\.m\.)?(?=\s)`)) ||
      (found = find(String.raw`\s(?:um|at|ab|gegen)\s+(\d{1,2})\.(\d{2})\s*(uhr|am|pm|a\.m\.|p\.m\.)?(?=\s)`)) ||
      (found = find(String.raw`\s(\d{1,2})\.(\d{2})\s*(uhr|am|pm|a\.m\.|p\.m\.)(?=\s)`)) ||
      (found = find(String.raw`\s(?:um\s+|at\s+|ab\s+|gegen\s+)?(\d{1,2})()\s*(uhr|am|pm|a\.m\.|p\.m\.)(?=\s)`)) ||
      (found = find(String.raw`\s(?:um|at)\s+(\d{1,2})()()(?=\s)`))) {
      let value = clock(found[1], found[2] || undefined, found[3] || undefined)
      // "heute Abend 7 Uhr": 19:00.
      if (value && evening && !found[3]?.match(/am|pm/i) && Number(found[1]) < 12) value = clock(String(Number(found[1]) + 12), found[2] || undefined, undefined)
      if (value) {
        start = value
        take(found)
      }
    } else if ((found = find(String.raw`\s(?:um\s+|at\s+)?(?:mittags|noon|midday)(?=\s)`))) {
      start = '12:00'
      take(found)
    }
  }
  // Only an end ("bis 17 Uhr Abgabe"): that is the time it happens.
  if (start === null && end !== null) {
    start = end
    end = null
  }

  // The title: what is left, without the little words that only led to a date or time.
  // "in Köln" stays part of the title (Out of Scope: no place); punctuation left at the edges goes.
  const fillers = /^(?:um|am|at|on|ab|von|from|für|for|bis|until|-|–|[,;:])$/iu
  // A German "am" kept from the times stays with its place ("am Bahnhof").
  const words = rest.trim().split(/\s+/u).filter(Boolean)
  while (words.length && fillers.test(words[0])) words.shift()
  while (words.length && fillers.test(words[words.length - 1])) words.pop()
  let title = words.join(' ').replaceAll(KEEP, '').replace(/^[,;:]+\s*|\s*[,;:]+$/gu, '')
  // "zahnarzt" becomes "Zahnarzt"; "iPhone" and "eBay" stay as they are (EC-5).
  if (title && !/\p{Lu}/u.test(title.split(' ')[0])) title = title[0].toLocaleUpperCase() + title.slice(1)

  if (invalid) return { kind: 'noDate', text: typed }

  // The fixed rules (EC-2).
  if (date === null && weekday !== null) {
    let candidate = onOrAfter(today, weekday)
    if (candidate === today && (nextOne || !(start !== null && start > nowTime))) candidate = addDays(today, 7)
    date = candidate
  }
  if (date !== null && lastWeekday !== null) lastDay = onOrAfter(date, lastWeekday)
  if (repeat?.nth && date === null) {
    let first = nthOnOrAfter(today, repeat.nth.weekday, repeat.nth.nth)
    if (first === today && !(start !== null && start > nowTime)) first = nthOnOrAfter(addDays(today, 1), repeat.nth.weekday, repeat.nth.nth)
    date = first
  }
  if (repeat?.days?.length && date === null) {
    // "jeden Montag": the next Monday to come; today when it is one and the time still comes.
    const firsts = repeat.days.map((day) => onOrAfter(today, day)).map((first) => (first === today && !(start !== null && start > nowTime) ? addDays(today, 7) : first))
    date = firsts.sort()[0]
  }
  if (date === null && start !== null) date = start > nowTime ? today : addDays(today, 1)
  if (date === null && repeat) date = today
  if (date === null) return { kind: 'noDate', text: typed }
  if (until === 'invalid') return { kind: 'noDate', text: typed }
  let rule: Rule | null = null
  if (repeat) {
    rule = { ...repeat.rule }
    if (until) {
      rule.end = 'until'
      rule.until = until
    }
    if (rule.freq === 'weekly') {
      if (!repeat.days?.length) rule.weekdays = [weekdayOf(date)]
      else if (!repeat.days.includes(weekdayOf(date))) {
        // A date that is none of the days: the series starts on the first of them after it.
        date = repeat.days.map((day) => onOrAfter(date as string, day)).sort()[0]
      }
    }
  }

  const allDay = start === null
  let problem: Extract<Understood, { kind: 'event' }>['problem'] = null
  let endDate = date
  let defaultLength = false
  if (start !== null) {
    // Through the zone: a time that does not exist the night the clocks change moves on (TIME-3 EC-1).
    const begin = instantOf(date, start, context.zone)
    const shown = wallOf(begin, context.zone)
    date = shown.date
    start = shown.time
    if (end !== null) {
      endDate = lastDay ?? date
      if (endDate === date && minutesOf(end) <= minutesOf(start)) {
        // "23 bis 1 Uhr": an evening into the night ends the next day; "15 bis 14 Uhr" is a mistake (EC-9).
        if (minutesOf(start) >= 17 * 60 && minutesOf(end) <= 6 * 60) endDate = addDays(date, 1)
        else problem = 'end'
      }
    } else if (lastDay !== null) {
      endDate = lastDay
      defaultLength = length === null
      end = wallOf(instantOf(lastDay, start, context.zone) + (length ?? DEFAULT_MINUTES) * 60_000, context.zone).time
    } else {
      defaultLength = length === null
      const finish = wallOf(begin + (length ?? DEFAULT_MINUTES) * 60_000, context.zone)
      endDate = finish.date
      end = finish.time
    }
  }
  if (lastDay !== null && start === null) {
    endDate = lastDay
    if (lastDay < date) problem = 'end'
  }
  if (rule?.end === 'until' && rule.until && rule.until < date) problem = 'end'
  // A length without a time cannot be placed: nothing is dropped silently, Enter opens the event.
  if (!problem && start === null && length !== null) problem = 'noTime'
  if (!problem && !title) problem = 'title'
  // Too long is said before anything else: Enter must not open the panel with a title it would refuse (EC-4).
  if (title.length > TITLE_MAX) problem = 'long'
  return { kind: 'event', title, allDay, date, start, endDate, end: allDay ? null : end, defaultLength, rule, calendarId, tag, problem }
}
