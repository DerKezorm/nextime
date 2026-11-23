/** TIME-12, a new event by a click into free room (asked for while testing): the free space of a day in the month opens "Neuer Termin" on that day, today at the next full hour, other
 * days at 09:00, an hour long; not on the day's number, an event or "+n", not on a phone, not without a connection. The
 * all-day row of Day and Week makes an all-day event. And the side panel shows start and end one below the other, each
 * with date and time (the year was cut off). Today is Thursday 26 November 2026, 16:05 in Berlin (`viewFakes`). */
import { act } from 'react'

import App from './App'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { markUnreachable, resetConnection } from './features/offline/connection'
import { setViewDay } from './features/views/state'
import { AuthProvider } from './state/auth'
import { box, render, settle, start, stop } from './testing'
import { at, NOW, theWeek, views, type Fake } from './viewFakes'

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  localStorage.clear()
  setViewDay(null)
  resetConnection()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  resetConnection()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function open(where: string, fake: Fake = { rows: theWeek() }): Promise<void> {
  views(fake)
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const cell = (date: string) => box.querySelector<HTMLElement>(`[data-testid="month-day"][data-date="${date}"]`)!
const value = (id: string) => (one(id) as HTMLInputElement | null)?.value
async function tap(element: Element | null): Promise<void> {
  await act(async () => (element as HTMLElement).click())
  await settle()
}

describe('TIME-12 create by click', () => {
  it('TIME-12 create by click the free space of today in the month opens a new event at the next full hour', async () => {
    await open('/month', { rows: [] })
    expect(cell('2026-11-26').hasAttribute('data-addable')).toBe(true)
    await tap(cell('2026-11-26'))
    expect(one('event-panel')).not.toBeNull()
    expect(value('field-start-date')).toBe('2026-11-26')
    expect(value('field-start-time')).toBe('17:00')
    expect(value('field-end-time')).toBe('18:00')
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('false')
  })

  it('TIME-12 create by click another day starts at 09:00, an hour long', async () => {
    await open('/month', { rows: [] })
    await tap(cell('2026-12-03'))
    expect(value('field-start-date')).toBe('2026-12-03')
    expect(value('field-start-time')).toBe('09:00')
    expect(value('field-end-time')).toBe('10:00')
  })

  it('TIME-12 create by click the day number, an event and "+n" keep their own way', async () => {
    const many = [1, 2, 3, 4].map((hour) => at(1, `Termin ${hour}`, `2026-11-24T0${hour}:00:00Z`, `2026-11-24T0${hour}:30:00Z`, { id: 50 + hour }))
    await open('/month', { rows: many })
    await tap(cell('2026-11-24').querySelector('[data-testid="month-more"]'))
    expect(one('event-panel')).toBeNull()
    await tap(document.querySelector('[data-testid="panel-close"]') ?? document.body)
    await tap(cell('2026-11-24').querySelector('[data-testid="month-event"]'))
    // The event itself opens, never "Neuer Termin".
    expect(one('event-panel')?.textContent).not.toContain('Neuer Termin')
    await tap(cell('2026-11-25').querySelector('[data-testid="month-day-number"]'))
    expect(one('view-day')).not.toBeNull()
  })

  it('TIME-12 create by click not on a phone, and not without a connection', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    await open('/month', { rows: [] })
    expect(cell('2026-12-03').hasAttribute('data-addable')).toBe(false)
    await tap(cell('2026-12-03'))
    expect(one('event-panel')).toBeNull()
    vi.unstubAllGlobals()
    await act(async () => markUnreachable())
    await settle()
    // Without a connection no pointer and no "+" either (the panel would not open for something new).
    expect(cell('2026-12-03').hasAttribute('data-addable')).toBe(false)
    await tap(cell('2026-12-03'))
    expect(one('event-panel')).toBeNull()
  })

  it('TIME-12 create by click into the all-day row of the week makes an all-day event that day', async () => {
    await open('/week', { rows: [] })
    const cells = [...box.querySelectorAll<HTMLElement>('[data-testid="all-day-cell"]')]
    await tap(cells[4])
    expect(value('field-start-date')).toBe('2026-11-27')
    expect(value('field-end-date')).toBe('2026-11-27')
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('true')
    expect(one('field-start-time')).toBeNull()
  })

  it('TIME-12 create by click not on a phone in the all-day row of the week either', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    await open('/week', { rows: [] })
    const row = one('all-day-row')!
    expect(row.className).not.toContain('nt-all-day-addable')
    await tap([...box.querySelectorAll<HTMLElement>('[data-testid="all-day-cell"]')][1])
    expect(one('event-panel')).toBeNull()
    vi.unstubAllGlobals()
  })

  it('TIME-12 create by click today after 23:00 stays on that day, never 09:00 already past', async () => {
    vi.setSystemTime(new Date('2026-11-26T22:20:00Z'))
    await open('/month', { rows: [] })
    await tap(cell('2026-11-26'))
    expect(value('field-start-date')).toBe('2026-11-26')
    expect(value('field-start-time')).toBe('23:00')
    expect(value('field-end-time')).toBe('23:59')
  })

  it('TIME-12 create by click from the all-day row, "Ganztägig" unticked, has an end time', async () => {
    await open('/week', { rows: [] })
    await tap([...box.querySelectorAll<HTMLElement>('[data-testid="all-day-cell"]')][4])
    await act(async () => (one('switch-all-day') as HTMLInputElement).click())
    await settle()
    expect(value('field-start-time')).toBe('09:00')
    expect(value('field-end-time')).toBe('10:00')
  })

  it('TIME-12 create by click the panel shows start and end each in a row of its own, date beside time', async () => {
    await open('/month', { rows: [] })
    await tap(cell('2026-12-03'))
    const times = one('times')!
    const rows = [...times.querySelectorAll('[data-testid^="time-row-"]')]
    expect(rows.map((row) => row.getAttribute('data-testid'))).toEqual(['time-row-start', 'time-row-end'])
    expect(rows[0].querySelector('[data-testid="field-start-date"]')?.className).toContain('nt-date')
    expect(rows[0].querySelector('[data-testid="field-start-time"]')?.className).toContain('nt-time')
    expect(times.className).not.toContain('grid-cols-2')
  })
})
