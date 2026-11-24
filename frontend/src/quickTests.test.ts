/**
 * TIME-10 for the iPhone app (TIME-29, interface 0.5): `docs/quick-tests.json` holds the sentences of Quick entry
 * with the moment, the zone and the calendars they are read with, and what `understand` makes of them. The app
 * checks its own reading against the same file; this test keeps the file true to the web. After a change of
 * `understand.ts` that is meant: `QUICK_TESTS_WRITE=1 npx vitest run src/quickTests.test.ts` writes the answers anew.
 */
import { describe, expect, it } from 'vitest'

import { understand, type Context, type Understood } from './features/quick/understand'

type Files = {
  readFileSync: (file: string, encoding: string) => string
  writeFileSync: (file: string, text: string, encoding: string) => void
}
const fsName = 'node:fs'
const files = (await import(/* @vite-ignore */ fsName)) as Files
const FILE = '../docs/quick-tests.json'

type Expected = {
  title: string | null
  all_day: boolean | null
  /** `YYYY-MM-DD` for a whole day, otherwise `YYYY-MM-DDTHH:MM` in the zone. */
  start: string | null
  /** The last day of a whole-day event, otherwise `YYYY-MM-DDTHH:MM`. */
  end: string | null
  default_length: boolean | null
  rule: unknown
  calendar: number | null
  tag: { text: string; problem: string } | null
  error: string | null
}

type Case = {
  now: string
  zone: string
  lang: 'de' | 'en'
  text: string
  calendars?: Context['calendars']
  expect?: Expected
}

const ERRORS: Record<string, string> = {
  end: 'end_before_start',
  title: 'no_title',
  long: 'title_too_long',
  noTime: 'length_without_time',
}

/** What the file says of an understood sentence, in the words of the app's interface. */
export function expected(found: Understood): Expected {
  const none = { title: null, all_day: null, start: null, end: null, default_length: null, rule: null, calendar: null, tag: null }
  if (found.kind === 'empty') return { ...none, error: 'empty' }
  if (found.kind === 'noDate') return { ...none, error: 'no_date' }
  const at = (date: string, time: string | null) => (found.allDay || time === null ? date : `${date}T${time}`)
  return {
    title: found.title,
    all_day: found.allDay,
    start: at(found.date, found.start),
    end: at(found.endDate, found.end),
    default_length: found.defaultLength,
    rule: found.rule,
    calendar: found.calendarId,
    tag: found.tag,
    error: found.problem === null ? null : ERRORS[found.problem],
  }
}

function contextOf(entry: Case): Context {
  return { now: new Date(entry.now).getTime(), zone: entry.zone, calendars: entry.calendars ?? [] }
}

const cases = JSON.parse(files.readFileSync(FILE, 'utf-8')) as Case[]

if (import.meta.env.QUICK_TESTS_WRITE === '1') {
  const written = cases.map((entry) => ({ ...entry, expect: expected(understand(entry.text, contextOf(entry))) }))
  files.writeFileSync(FILE, JSON.stringify(written, null, 1) + '\n', 'utf-8')
}

describe('TIME-29 the sentences of quick entry for the app', () => {
  it('TIME-29 TIME-10 the file names every sentence once with its answer, in German and English', () => {
    expect(cases.length).toBeGreaterThan(100)
    expect(new Set(cases.map((entry) => JSON.stringify([entry.text, entry.now, entry.zone, entry.calendars]))).size).toBe(cases.length)
    expect(new Set(cases.map((entry) => entry.lang))).toEqual(new Set(['de', 'en']))
    expect(cases.every((entry) => entry.expect !== undefined)).toBe(true)
  })

  it.each(cases.map((entry) => [entry.text, entry] as const))('TIME-29 TIME-10 "%s" as the web reads it', (_text, entry) => {
    expect(expected(understand(entry.text, contextOf(entry)))).toEqual(entry.expect)
  })
})
