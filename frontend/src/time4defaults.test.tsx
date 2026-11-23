/** TIME-4, "Vorgaben für neue Termine" (decided while testing, 2026-11-23): a new event of a calendar starts with that
 * calendar's defaults (whole day, length, repeat, reminders, busy) on every way it is made; what the member gives himself
 * wins; another calendar chosen brings its defaults for everything not touched. The birthday case: whole day, every
 * year, the day before at 18:00, free. Today is Thursday 26 November 2026, 16:05 in Berlin. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import i18n from './i18n'
import { forgetPrefs, setPref } from './lib/devicePref'
import { setViewDay } from './features/views/state'
import { AuthProvider } from './state/auth'
import { box, calls, choose, click, render, settle, start, stop, type } from './testing'
import { NOW, views } from './viewFakes'

const BIRTHDAYS = { all_day: true, rule: 'yearly' as const, reminders: [360], busy: false }
const LISTS = calendarList([
  calendar(1, 'Familie', { default: true }),
  calendar(2, 'Geburtstage', { color: 'pink', defaults: BIRTHDAYS }),
  calendar(3, 'Arbeit', { color: 'teal', defaults: { minutes: 30, rule: 'weekly' } }),
])

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  localStorage.clear()
  setViewDay(null)
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

async function open(where = '/'): Promise<void> {
  views({ rows: [], calendars: LISTS, more: (method, path) => (method === 'POST' && path === '/api/events' ? { status: 201, body: { id: 77, title: 'x' } } : undefined) })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const value = (id: string) => (one(id) as HTMLInputElement | null)?.value
const press = async (key: string) => act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key })))
const posted = () => calls.filter((call) => call.method === 'POST' && call.path === '/api/events').map((call) => call.body as Record<string, unknown>)

async function save(): Promise<void> {
  type(one('field-title') as HTMLInputElement, 'Oma')
  await click(one('panel-save'))
  await settle()
}

describe('TIME-4 defaults', () => {
  it('TIME-4 defaults the birthday case: whole day, every year, the day before at 18:00, free', async () => {
    setPref('only', 1, '2')
    await open()
    await press('n')
    expect(value('field-calendar')).toBe('2')
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('true')
    expect(one('switch-busy')?.getAttribute('aria-checked')).toBe('false')
    expect(one('field-rule')?.textContent).toContain('Jedes Jahr')
    expect([...document.querySelectorAll('[data-testid="reminder-chip"]')].map((chip) => chip.textContent)).toEqual(['am Vortag um 18:00×'])
    await save()
    expect(posted()[0]).toMatchObject({ calendar_id: 2, all_day: true, busy: false, reminders: [360], rule: { freq: 'yearly', interval: 1, end: 'never' } })
  })

  it('TIME-4 defaults a time dragged wins over "whole day", a click does not, the rest comes from the calendar', async () => {
    setPref('only', 1, '2')
    await open('/week')
    const column = box.querySelectorAll<HTMLElement>('[data-testid="grid-column"]')[3]
    const fire = async (kind: string, y: number) => act(async () => void column.dispatchEvent(new MouseEvent(kind, { bubbles: true, clientX: 200, clientY: y, button: 0 })))
    // A click is no time of the member's (review of the additions, 2026-11-23): the calendar's "whole day" holds.
    await fire('pointerdown', 14 * 36)
    await fire('pointerup', 14 * 36)
    await settle()
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('true')
    expect(one('field-rule')?.textContent).toContain('Jedes Jahr')
    await click(one('panel-close'))
    // Dragged over more than one step: the time dragged wins.
    await fire('pointerdown', 14 * 36)
    await fire('pointermove', 15.5 * 36)
    await fire('pointerup', 15.5 * 36)
    await settle()
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('false')
    expect(value('field-start-time')).toBe('14:00')
    expect(value('field-end-time')).toBe('15:30')
    expect(one('field-rule')?.textContent).toContain('Jedes Jahr')
  })

  it('TIME-4 defaults another calendar brings its defaults for what was not touched', async () => {
    await open()
    await press('n')
    expect(value('field-calendar')).toBe('1')
    expect(value('field-end-time')).toBe('18:00')
    await choose(one('field-calendar') as HTMLSelectElement, '3')
    expect(value('field-end-time')).toBe('17:30')
    expect(one('field-rule')?.textContent).toContain('Jeden Donnerstag')
    // Whole day chosen by hand stays when the calendar changes again.
    await click(one('switch-all-day'))
    await choose(one('field-calendar') as HTMLSelectElement, '1')
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('true')
    expect(one('field-rule')?.textContent).not.toContain('Donnerstag')
  })

  it('TIME-4 defaults the quick field: the length and the repeat of the calendar, unless typed', async () => {
    await open()
    const field = one('quick-entry') as HTMLInputElement
    type(field, 'morgen 15 uhr zahnarzt #Arbeit')
    expect(one('quick-when')?.textContent).toContain('15:00–15:30')
    await act(async () => (field.form as HTMLFormElement).requestSubmit())
    await settle()
    expect(posted()[0]).toMatchObject({ calendar_id: 3, start: '2026-11-27T15:00', end: '2026-11-27T15:30', rule: { freq: 'weekly' } })
    type(field, 'morgen 15 uhr zahnarzt 2 std #Arbeit')
    await act(async () => (field.form as HTMLFormElement).requestSubmit())
    await settle()
    expect(posted()[1]).toMatchObject({ end: '2026-11-27T17:00' })
  })

  it('TIME-4 defaults the calendar dialog sets them under "Neue Termine in diesem Kalender"', async () => {
    await open()
    await click(box.querySelector('[data-testid="add-calendar"]'))
    await click(document.querySelector('[data-testid="way-own"]'))
    const dialog = one('calendar-dialog')!
    expect(dialog.querySelector('[data-testid="new-event-defaults"] legend')?.textContent).toBe('Neue Termine in diesem Kalender')
    type(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!, 'Geburtstage 2')
    await click(dialog.querySelector('[data-testid="defaults-all-day"]'))
    await choose(dialog.querySelector('[data-testid="defaults-rule"]') as HTMLSelectElement, 'yearly')
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/calendars')?.body).toMatchObject({ defaults: { all_day: true, rule: 'yearly' } })
  })

  it('TIME-4 defaults the length is there to choose for events with a time, and goes at a whole day', async () => {
    await open()
    await click(box.querySelector('[data-testid="add-calendar"]'))
    await click(document.querySelector('[data-testid="way-own"]'))
    const dialog = one('calendar-dialog')!
    const length = dialog.querySelector<HTMLSelectElement>('[data-testid="defaults-length"]')!
    expect(length.disabled).toBe(false)
    expect(dialog.querySelector('[data-testid="defaults-fields"]')!.className).toContain('sm:grid-cols-2')
    await choose(length, '45')
    type(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!, 'Termine')
    await click(dialog.querySelector('[data-testid="defaults-all-day"]'))
    expect(dialog.querySelector('[data-testid="defaults-length"]')).toBeNull()
    expect(dialog.querySelector('[data-testid="defaults-fields"]')!.className).not.toContain('sm:grid-cols-2')
    await click(dialog.querySelector('[data-testid="defaults-all-day"]'))
    await choose(dialog.querySelector<HTMLSelectElement>('[data-testid="defaults-length"]')!, '90')
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/calendars')?.body).toMatchObject({ defaults: { minutes: 90 } })
  })
})

describe('TIME-4 defaults after the review', () => {
  it('TIME-4 defaults a click in Day or Week takes the length of the calendar, also after another calendar is chosen', async () => {
    setPref('only', 1, '3')
    await open('/week')
    const column = box.querySelectorAll<HTMLElement>('[data-testid="grid-column"]')[3]
    const fire = async (kind: string, y: number) => act(async () => void column.dispatchEvent(new MouseEvent(kind, { bubbles: true, clientX: 200, clientY: y, button: 0 })))
    await fire('pointerdown', 10 * 36)
    await fire('pointerup', 10 * 36)
    await settle()
    expect(value('field-start-time')).toBe('10:00')
    expect(value('field-end-time')).toBe('10:30')
    // "Familie" has no length of its own: the usual hour; "Geburtstage" makes it a whole day.
    await choose(one('field-calendar') as HTMLSelectElement, '1')
    expect(value('field-end-time')).toBe('11:00')
    await choose(one('field-calendar') as HTMLSelectElement, '2')
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('true')
  })

  it('TIME-4 defaults reminders per kind: one for whole days never goes to an event with a time, and back with "Ganztägig"', async () => {
    setPref('only', 1, '2')
    await open('/week')
    const column = box.querySelectorAll<HTMLElement>('[data-testid="grid-column"]')[3]
    const fire = async (kind: string, y: number) => act(async () => void column.dispatchEvent(new MouseEvent(kind, { bubbles: true, clientX: 200, clientY: y, button: 0 })))
    await fire('pointerdown', 14 * 36)
    await fire('pointermove', 15.5 * 36)
    await fire('pointerup', 15.5 * 36)
    await settle()
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('false')
    // "Am Vortag um 18:00" is for whole days; this event has a time and gets none of it.
    expect(document.querySelectorAll('[data-testid="reminder-chip"]')).toHaveLength(0)
    await click(one('switch-all-day'))
    expect([...document.querySelectorAll('[data-testid="reminder-chip"]')].map((chip) => chip.textContent)).toEqual(['am Vortag um 18:00×'])
    await click(one('switch-all-day'))
    expect(document.querySelectorAll('[data-testid="reminder-chip"]')).toHaveLength(0)
  })

  it('TIME-4 defaults the calendar dialog has a reminder field for each kind', async () => {
    await open()
    await click(box.querySelector('[data-testid="add-calendar"]'))
    await click(document.querySelector('[data-testid="way-own"]'))
    const timed = one('defaults-reminders-timed')!
    const whole = one('defaults-reminders-all-day')!
    expect(timed.textContent).toContain('Erinnerungen mit Uhrzeit')
    expect(whole.textContent).toContain('Erinnerungen ganztägig')
  })
})

describe('TIME-4 defaults and "Mehr …"', () => {
  it('TIME-4 defaults "Mehr …" hands over only what the text said; defaults stay defaults for another calendar', async () => {
    await open()
    type(one('quick-entry') as HTMLInputElement, 'Kaffee morgen 15 Uhr #Arbeit')
    await click(one('quick-more'))
    expect(value('field-calendar')).toBe('3')
    expect(value('field-end-time')).toBe('15:30')
    expect(one('field-rule')?.textContent).toContain('Jeden Freitag')
    await choose(one('field-calendar') as HTMLSelectElement, '1')
    expect(value('field-end-time')).toBe('16:00')
    expect(one('field-rule')?.textContent).not.toContain('Freitag')
  })

  it('TIME-4 defaults "Mehr …" keeps a length and a repeat that were typed', async () => {
    await open()
    type(one('quick-entry') as HTMLInputElement, 'Kaffee morgen 15 Uhr 2h jede Woche #Arbeit')
    await click(one('quick-more'))
    await choose(one('field-calendar') as HTMLSelectElement, '1')
    expect(value('field-end-time')).toBe('17:00')
    expect(one('field-rule')?.textContent).toContain('Freitag')
  })
})

describe('TIME-4 defaults, small points of the review', () => {
  it('TIME-4 defaults a weekly repeat of the defaults follows a new day; "free" of the defaults holds for both kinds', async () => {
    setPref('only', 1, '3')
    await open()
    await press('n')
    expect(one('field-rule')?.textContent).toContain('Donnerstag')
    const date = one('field-start-date') as HTMLInputElement
    type(date, '2026-11-30')
    expect(one('field-rule')?.textContent).toContain('Montag')
    await click(one('panel-close'))
    stop()
    start()
    setPref('only', 1, '2')
    await open()
    await press('n')
    expect(one('switch-busy')?.getAttribute('aria-checked')).toBe('false')
    await click(one('switch-all-day'))
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('false')
    expect(one('switch-busy')?.getAttribute('aria-checked')).toBe('false')
  })
})
