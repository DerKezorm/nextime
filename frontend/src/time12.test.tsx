/** TIME-12 Day, Week, Month: the head and its switch, the keys, 7 to 22 o'clock, today and the weekend, events side by
 * side, the all-day row, the month, dragging and stretching, new events from the grid, the jump to a calendar week,
 * a phone, long titles, code as text, the change of the clocks. */
import { act } from 'react'

import App from './App'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { isoWeekOf, mondayOfWeek, monthWeeks, weeksInYear } from './features/views/dates'
import { columns, moved, quarter, timedParts } from './features/views/layout'
import { setViewDay } from './features/views/state'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, render, settle, start, stop, type } from './testing'
import { at, BERLIN, NOW, theWeek, views, type Fake } from './viewFakes'

/** The style sheet, read as a file (`node:fs` through a name, as `i18n/used.test.ts` does it). */
const fsName = 'node:fs'
const { readFileSync } = (await import(/* @vite-ignore */ fsName)) as { readFileSync: (file: string, encoding: string) => string }

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

async function open(where: string, fake: Fake = { rows: theWeek() }): Promise<void> {
  views(fake)
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
}

function as(display: Partial<typeof BERLIN>, fake: Fake = { rows: theWeek() }): Fake {
  return { ...fake, more: (_method, path) => (path === '/api/auth/me' ? { status: 200, body: me({ id: 1, display: { ...BERLIN, ...display } }) } : undefined) }
}

const all = (id: string, scope: ParentNode = box) => [...scope.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
/** The text, with the thin spaces of a period ("30. November – 6. Dezember") as plain ones. */
const text = (id: string) => (one(id)?.textContent ?? '').replaceAll(String.fromCharCode(0x2009), ' ')
const press = async (key: string) => act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key })))

async function pointer(target: Element, type: string, x: number, y: number): Promise<void> {
  await act(async () => void target.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 })))
}

/** A drag from (x, y) by (dx, dy) pixels; one hour is 36 px in a test, a column 100 px after the 44 px of hours. */
async function drag(target: Element, x: number, y: number, dx: number, dy: number): Promise<void> {
  await pointer(target, 'pointerdown', x, y)
  if (dx || dy) await pointer(target, 'pointermove', x + dx, y + dy)
  await pointer(target, 'pointerup', x + dx, y + dy)
  await settle()
}

const patches = () => calls.filter((call) => call.method === 'PATCH')

async function jump(week: string, year: string): Promise<void> {
  type(one('jump-week') as HTMLInputElement, week)
  type(one('jump-year') as HTMLInputElement, year)
  await act(async () => (one('week-jump') as HTMLFormElement).requestSubmit())
  await settle()
}

describe('TIME-12 the head', () => {
  it('TIME-12 AC-1 the switch changes the view without loading and keeps the day; ‹ › step; "Heute" goes back', async () => {
    await open('/week')
    expect(text('period')).toBe('23.–29. November 2026')
    expect(text('week-number')).toBe('KW 48')
    await click(one('step-on'))
    expect(text('period')).toBe('30. November – 6. Dezember 2026')
    expect(text('week-number')).toBe('KW 49')
    await click(byText('Tag', one('view-switch')!))
    expect(one('view-day')).not.toBeNull()
    // The day moved along with the week: Thursday a week on.
    expect(text('period')).toBe('Donnerstag, 3. Dezember 2026')
    await click(one('step-back'))
    expect(text('period')).toBe('Mittwoch, 2. Dezember 2026')
    await click(byText('Monat', one('view-switch')!))
    expect(text('period')).toBe('Dezember 2026')
    await click(one('step-on'))
    expect(text('period')).toBe('Januar 2027')
    await click(one('to-today'))
    expect(text('period')).toBe('November 2026')
    // Never loaded anew: the account was asked once.
    expect(calls.filter((call) => call.path === '/api/auth/me')).toHaveLength(1)
  })

  it('TIME-12 AC-1 the week begins on the chosen first day', async () => {
    await open('/week', as({ week_start: 0 }))
    expect(all('week-day').map((day) => day.firstElementChild!.textContent)).toEqual(['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'])
    expect(text('period')).toBe('22.–28. November 2026')
  })

  it('TIME-12 AC-6 the keys T, W, M, H and the arrows change view and period', async () => {
    await open('/')
    await press('w')
    expect(one('view-week')).not.toBeNull()
    await press('ArrowRight')
    expect(text('period')).toBe('30. November – 6. Dezember 2026')
    await press('t')
    expect(text('period')).toBe('Donnerstag, 3. Dezember 2026')
    await press('ArrowLeft')
    expect(text('period')).toBe('Mittwoch, 2. Dezember 2026')
    await press('m')
    expect(text('period')).toBe('Dezember 2026')
    await press('h')
    expect(one('view-today')).not.toBeNull()
    await press('n')
    expect(one('event-panel')).not.toBeNull()
  })
})

describe('TIME-12 day and week', () => {
  it('TIME-12 AC-7 7 to 22 o\'clock in view, earlier hours by scrolling, the hours in 24 hours', async () => {
    await open('/week', { rows: [at(1, 'Früh', '2026-11-26 06:00', '2026-11-26 06:45')] })
    const hour = Number(one('grid-body')!.dataset.hour)
    expect(hour).toBe(36)
    expect(one('grid-scroll')!.scrollTop).toBe(7 * hour)
    const early = one('grid-event')!
    expect(early.style.getPropertyValue('--s')).toBe('6')
    expect(all('hour-label').map((label) => label.textContent).slice(0, 3)).toEqual(['1:00', '2:00', '3:00'])
  })

  it('TIME-12 AC-7 the hours in 12 hours when the account says so', async () => {
    await open('/day', as({ clock: '12' }))
    const labels = all('hour-label').map((label) => label.textContent)
    expect(labels.slice(0, 2)).toEqual(['1 AM', '2 AM'])
    expect(labels[11]).toBe('12 PM')
    expect(labels[22]).toBe('11 PM')
  })

  it('TIME-12 AC-9 today is marked, its column tinted and the line of now at 16:05', async () => {
    await open('/week')
    const heads = all('week-day')
    expect(heads[3].getAttribute('aria-current')).toBe('date')
    const columns = all('grid-column')
    expect(columns[3].className).toContain('nt-today-column')
    expect(columns[2].className).not.toContain('nt-today-column')
    expect(Number(one('now-line')!.style.getPropertyValue('--s'))).toBeCloseTo(16 + 5 / 60)
    expect(all('now-line')).toHaveLength(1)
  })

  it('TIME-12 AC-12 Saturday and Sunday tinted in slate blue whatever the first day; today stays stronger on a weekend', async () => {
    await open('/week')
    const tinted = (cells: HTMLElement[]) => cells.map((cell) => cell.className.includes('nt-weekend'))
    expect(tinted(all('grid-column'))).toEqual([false, false, false, false, false, true, true])
    await open('/week', as({ week_start: 0 }))
    expect(tinted(all('grid-column'))).toEqual([true, false, false, false, false, false, true])
    await open('/month')
    const days = all('month-day')
    expect(days.filter((day) => day.className.includes('nt-weekend')).map((day) => day.dataset.date!.slice(8))).toEqual(['31', '01', '07', '08', '14', '15', '21', '22', '28', '29', '05', '06'])
    vi.setSystemTime(new Date('2026-11-28T10:00:00Z'))
    setViewDay(null)
    await open('/day')
    expect(all('grid-column')[0].className).toContain('nt-weekend')
    expect(all('grid-column')[0].className).toContain('nt-today-column')
    // The tones of the design: light 0.05 and 0.10, dark 0.045 and 0.09; today's after the weekend's, so it wins.
    const css = readFileSync('src/styles/index.css', 'utf-8')
    expect(css).toContain('--color-weekend: rgb(74 100 145 / 0.05)')
    expect(css).toContain('--color-today-column: rgb(74 100 145 / 0.1)')
    expect(css).toContain('--color-weekend: rgb(159 180 216 / 0.045)')
    expect(css).toContain('--color-today-column: rgb(159 180 216 / 0.09)')
    expect(css.indexOf('.nt-today-column {')).toBeGreaterThan(css.indexOf('.nt-weekend {'))
  })

  it('TIME-12 AC-12 a phone week is tinted the same', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    await open('/week')
    expect(all('grid-column').map((cell) => cell.className.includes('nt-weekend'))).toEqual([false, false, true])
  })

  it('TIME-12 AC-12 the hour lines lie over the tint of the weekend and of today, the head stands flush with the grid', async () => {
    await open('/week')
    const css = readFileSync('src/styles/index.css', 'utf-8')
    const rule = (selector: string) => {
      const start = css.indexOf(`${selector} {`)
      return css.slice(start, css.indexOf('}', start))
    }
    // The lines are a layer of their own above the column's ground, in a translucent color in both themes.
    expect(rule('.nt-column::before')).toContain('background-image: repeating-linear-gradient(to bottom, var(--color-grid-line)')
    expect(rule('.nt-column')).not.toContain('background-image')
    // The tints set a ground color only, never a picture over the lines.
    expect(rule('.nt-weekend').replace(/\s+/g, ' ').trim()).toBe('.nt-weekend { background-color: var(--color-weekend);')
    expect(rule('.nt-today-column').replace(/\s+/g, ' ').trim()).toBe('.nt-today-column { background-color: var(--color-today-column);')
    expect(css).toContain('--color-grid-line: rgb(255 255 255 / 0.08)')
    expect(css).toContain('--color-grid-line: rgb(20 24 40 / 0.09)')
    expect(all('grid-column').every((column) => column.className.includes('nt-column'))).toBe(true)
    // The head and the all-day row keep the room of the grid's scroll bar.
    expect(rule('.nt-gutter')).toContain('scrollbar-gutter: stable')
    expect(box.querySelector('.nt-grid-head')!.className).toContain('nt-gutter')
    expect(one('all-day-row')!.className).toContain('nt-gutter')
    expect(one('grid-scroll')!.className).toContain('[scrollbar-gutter:stable]')
  })

  it('TIME-12 AC-4 three overlapping events stand side by side, a fourth shows "+1" and its list', async () => {
    const rows = ['A', 'B', 'C', 'D'].map((name) => at(1, `Termin ${name}`, '2026-11-26 10:00', '2026-11-26 11:00'))
    await open('/week', { rows })
    const thursday = all('grid-column')[3]
    const events = all('grid-event', thursday)
    expect(events).toHaveLength(3)
    expect(events.map((event) => event.style.getPropertyValue('--w'))).toEqual([String(1 / 3), String(1 / 3), String(1 / 3)])
    expect(all('grid-more', thursday)[0].textContent).toBe('+1')
    await click(all('grid-more', thursday)[0])
    expect(text('day-list')).toContain('Termin D')
  })

  it('TIME-12 EC-1 an event over midnight stands on both days and reads as one', async () => {
    await open('/week', { rows: [at(1, 'Nachtschicht', '2026-11-26 23:00', '2026-11-27 01:00')] })
    const [first, second] = all('grid-event')
    expect(first.closest('[data-date]')!.getAttribute('data-date')).toBe('2026-11-26')
    expect(first.className).toContain('nt-continues-after')
    expect(second.closest('[data-date]')!.getAttribute('data-date')).toBe('2026-11-27')
    expect(second.className).toContain('nt-continues-before')
    expect(second.style.getPropertyValue('--s')).toBe('0')
  })

  it('TIME-12 AC-8 EC-5 the all-day row: background small, a whole day, a holiday over the week as one bar', async () => {
    await open('/week', { rows: [...theWeek(), at(2, 'Urlaub', '2026-11-24', '2026-12-02'), at(1, 'Ausflug', '2026-11-27', '2026-11-27')] })
    const cells = all('all-day-cell')
    expect(cells[3].textContent).toBe('Gelbe Tonne')
    expect(cells[6].textContent).toBe('1. Advent')
    const bars = all('all-day-bar')
    const holiday = bars.find((bar) => bar.textContent === 'Urlaub')!
    expect(holiday.style.gridColumn).toBe('3 / 9')
    expect(holiday.className).toContain('nt-cut-end')
    expect(bars.find((bar) => bar.textContent === 'Ausflug')!.style.gridColumn).toBe('6 / 7')
    // Not in the grid of hours.
    expect(all('grid-event').map((event) => event.textContent)).not.toContain('Urlaub')
  })

  it('TIME-12 EC-4 a full all-day row shows the first and "+n"; a click shows all', async () => {
    const rows = ['Eins', 'Zwei', 'Drei', 'Vier', 'Fünf'].map((name) => at(1, name, '2026-11-26', '2026-11-26'))
    await open('/week', { rows })
    expect(all('all-day-bar')).toHaveLength(3)
    expect(text('all-day-more')).toBe('+2')
    await click(one('all-day-more'))
    expect(text('day-list')).toContain('Fünf')
  })

  it('TIME-12 AC-11 events show the color of their calendar, the person, "in 2 Kalendern" and "Belegt"', async () => {
    const twice = at(2, 'Elternabend', '2026-11-25 19:30', '2026-11-25 21:00', {
      sources: [
        { id: 1, occurrence: null, calendar_id: 2, writable: true, etag: '"a"' },
        { id: 2, occurrence: null, calendar_id: 1, writable: true, etag: '"b"' },
      ],
    })
    await open('/week', { rows: [...theWeek(), twice] })
    const events = all('grid-event')
    const dentist = events.find((event) => event.textContent!.includes('Zahnarzt Ben'))!
    expect(dentist.style.getPropertyValue('--k')).toBe('var(--color-cal-slate)')
    expect(dentist.querySelector('.nt-ev-person [data-testid="avatar-initial"]')!.textContent).toBe('A')
    const parents = events.find((event) => event.textContent!.includes('Elternabend'))!
    expect(parents.querySelector('[data-testid="in-calendars"]')!.textContent).toBe('in 2 Kalendern')
    const busy = events.find((event) => event.className.includes('nt-busy'))!
    expect(busy.textContent).toContain('Belegt')
    expect(busy.textContent).toContain('Anna')
  })

  it('TIME-12 EC-10 long titles break where the room ends and stand whole', async () => {
    const long = 'A very long title in English for a meeting that goes on and on '.repeat(3).trim()
    await open('/week', { rows: [at(1, long, '2026-11-26 10:00', '2026-11-26 12:00')] })
    const title = one('grid-event')!.querySelector('.nt-ev-title')!
    expect(title.textContent).toBe(long)
    expect(readFileSync('src/styles/index.css', 'utf-8')).toMatch(/\.nt-ev \.nt-ev-title \{[^}]*overflow-wrap: break-word/)
  })

  it('TIME-12 EC-11 a week with the change of the clocks keeps grid and times', async () => {
    setViewDay('2027-03-25')
    await open('/week', { rows: [at(1, 'Vorher', '2027-03-26 10:00', '2027-03-26 11:00'), at(1, 'Nachher', '2027-03-28 10:00', '2027-03-28 11:00')] })
    expect(text('period')).toBe('22.–28. März 2027')
    const events = all('grid-event')
    expect(events.map((event) => event.style.getPropertyValue('--s'))).toEqual(['10', '10'])
    expect(events[1].getAttribute('aria-label')).toContain('10:00–11:00')
  })

  it('TIME-12 EC-13 a title with code stays text in the grid and the month', async () => {
    const rows = [at(1, '<img src=x onerror=alert(1)>', '2026-11-26 10:00', '2026-11-26 11:00')]
    await open('/week', { rows })
    expect(box.querySelector('img[src="x"]')).toBeNull()
    expect(one('grid-event')!.textContent).toContain('<img src=x onerror=alert(1)>')
    await open('/month', { rows })
    expect(box.querySelector('img[src="x"]')).toBeNull()
  })
})

describe('TIME-12 dragging and new events', () => {
  // Thursday is the fourth column: x = 44 + 3 × 100 + 50.
  const THURSDAY = 394

  it('TIME-12 AC-2 EC-7 a drag moves in quarters of an hour and sends the version the grid holds', async () => {
    const training = at(7, 'Training', '2026-11-26 18:30', '2026-11-26 20:00')
    await open('/week', { rows: [training] })
    await drag(one('grid-event')!, THURSDAY, 700, 0, 27)
    expect(patches().map((call) => [call.path, call.body])).toEqual([
      [`/api/events/${training.id}`, { all_day: false, start: '2026-11-26T19:15', end: '2026-11-26T20:45', time_zone: 'Europe/Berlin', scope: 'all', occurrence: null, etag: training.etag }],
    ])
  })

  it('TIME-12 AC-2 a drag at the edge stretches the end; a drag sideways moves to another day', async () => {
    const training = at(7, 'Training', '2026-11-26 18:30', '2026-11-26 20:00')
    await open('/week', { rows: [training] })
    await drag(one('grid-event')!.querySelector('[data-edge]')!, THURSDAY, 700, 0, 18)
    expect(patches().at(-1)!.body).toMatchObject({ start: '2026-11-26T18:30', end: '2026-11-26T20:30' })
    await drag(one('grid-event')!, THURSDAY, 700, 100, 0)
    expect(patches().at(-1)!.body).toMatchObject({ start: '2026-11-27T18:30', end: '2026-11-27T20:00' })
  })

  it('TIME-12 AC-2 a series asks "Nur diesen / folgende / alle" before moving', async () => {
    const training = at(7, 'Training', '2026-11-26 18:30', '2026-11-26 20:00', { recurring: true, occurrence: '20261126T173000Z' })
    await open('/week', { rows: [training] })
    await drag(one('grid-event')!, THURSDAY, 700, 0, 36)
    expect(patches()).toHaveLength(0)
    expect(text('series-question')).toContain('26. November')
    expect(document.querySelector('[role="dialog"]')!.getAttribute('aria-label')).toBe('„Training“ auf 19:30 verschieben?')
    await click(byText('Nur diesen Termin', document))
    expect(patches()[0].body).toMatchObject({ scope: 'this', occurrence: '20261126T173000Z', start: '2026-11-26T19:30' })
  })

  it('TIME-12 EC-2 a read-only event and "Belegt" do not move and say why', async () => {
    await open('/week')
    const busy = all('grid-event').find((event) => event.className.includes('nt-busy'))!
    await drag(busy, THURSDAY, 520, 0, 36)
    expect(text('grid-note')).toBe('„Belegt“ ist nur lesbar und lässt sich nicht verschieben.')
    expect(patches()).toHaveLength(0)
    expect(busy.querySelector('[data-edge]')).toBeNull()
  })

  it('TIME-12 AC-3 a click into free room opens a new event of an hour there; a drag takes the time dragged', async () => {
    await open('/week', { rows: [] })
    const thursday = all('grid-column')[3]
    await drag(thursday, THURSDAY, 10 * 36 + 5, 0, 0)
    expect((one('field-start-date') as HTMLInputElement).value).toBe('2026-11-26')
    expect((one('field-start-time') as HTMLInputElement).value).toBe('10:00')
    expect((one('field-end-time') as HTMLInputElement).value).toBe('11:00')
    await click(one('panel-close'))
    await drag(all('grid-column')[4], THURSDAY + 100, 14 * 36, 0, 54)
    expect((one('field-start-date') as HTMLInputElement).value).toBe('2026-11-27')
    expect((one('field-start-time') as HTMLInputElement).value).toBe('14:00')
    expect((one('field-end-time') as HTMLInputElement).value).toBe('15:30')
  })

  it('TIME-12 EC-8 two quick clicks into free room make exactly one new event', async () => {
    await open('/week', { rows: [], more: (method, path) => (path === '/api/events' && method === 'POST' ? { status: 201, body: {} } : undefined) })
    const thursday = all('grid-column')[3]
    await drag(thursday, THURSDAY, 9 * 36, 0, 0)
    await drag(thursday, THURSDAY, 9 * 36, 0, 0)
    expect(all('event-panel', document)).toHaveLength(1)
    type(one('field-title') as HTMLInputElement, 'Probe')
    const save = one('panel-save')!
    await act(async () => {
      save.click()
      save.click()
    })
    await settle()
    expect(calls.filter((call) => call.method === 'POST' && call.path === '/api/events')).toHaveLength(1)
  })
})

describe('TIME-12 the month', () => {
  it('TIME-12 AC-10 EC-12 week column, background beside the number, quiet outside days, "1. Dez.", dot, time and title', async () => {
    await open('/month')
    expect(all('month-week-number').map((cell) => cell.textContent)).toEqual(['44', '45', '46', '47', '48', '49'])
    const days = all('month-day')
    expect(days).toHaveLength(42)
    expect(days.filter((day) => day.dataset.outside).map((day) => day.dataset.date)).toEqual([
      '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30', '2026-10-31',
      '2026-12-01', '2026-12-02', '2026-12-03', '2026-12-04', '2026-12-05', '2026-12-06',
    ])
    const first = days.find((day) => day.dataset.date === '2026-12-01')!
    expect(first.querySelector('[data-testid="month-day-number"]')!.textContent).toBe('1. Dez.')
    const advent = days.find((day) => day.dataset.date === '2026-11-29')!
    expect(advent.querySelector('[data-testid="month-background"]')!.textContent).toBe('1. Advent')
    const friday = days.find((day) => day.dataset.date === '2026-11-27')!
    expect(all('month-event', friday).map((event) => event.textContent)).toEqual(['08:15Werkstatt', '17:00Fußball Ben'])
    expect(all('month-event', friday)[0].querySelector('[data-color="orange"]')).not.toBeNull()
  })

  it('TIME-12 EC-12 February 2028 with the 29th and five rows; the column of weeks only when on', async () => {
    setViewDay('2028-02-10')
    await open('/month', as({ week_numbers: false }, { rows: [] }))
    expect(text('period')).toBe('Februar 2028')
    expect(all('month-week')).toHaveLength(5)
    expect(all('month-day').some((day) => day.dataset.date === '2028-02-29' && !day.dataset.outside)).toBe(true)
    expect(one('month-week-number')).toBeNull()
    expect(text('week-button')).toBe('Zu KW …')
  })

  it('TIME-12 AC-5 more than three events show three and "+n weitere"; a click shows all of them in the day', async () => {
    const rows = ['Eins', 'Zwei', 'Drei', 'Vier', 'Fünf'].map((name, index) => at(1, name, `2026-11-27 ${10 + index}:00`, `2026-11-27 ${10 + index}:30`))
    rows.push(...['A', 'B', 'C', 'D'].map((name, index) => at(1, name, `2026-11-30 ${10 + index}:00`, `2026-11-30 ${10 + index}:30`)))
    await open('/month', { rows })
    const day = (date: string) => all('month-day').find((cell) => cell.dataset.date === date)!
    expect(all('month-event', day('2026-11-27'))).toHaveLength(3)
    expect(all('month-more', day('2026-11-27'))[0].textContent).toBe('+2 weitere')
    expect(all('month-more', day('2026-11-30'))[0].textContent).toBe('+1 weiterer')
    await click(all('month-more', day('2026-11-27'))[0])
    expect(one('view-day')).not.toBeNull()
    expect(text('period')).toContain('27. November 2026')
  })

  it('TIME-12 AC-14 a week number in the month shows that week', async () => {
    await open('/month')
    await click(all('month-week-number')[5])
    expect(one('view-week')).not.toBeNull()
    expect(text('period')).toBe('30. November – 6. Dezember 2026')
  })

  it('TIME-12 EC-3 on a phone the month shows dots and the list of the day chosen', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    await open('/month')
    expect(all('month-event')).toHaveLength(0)
    const thursday = all('month-day').find((day) => day.dataset.date === '2026-11-26')!
    expect(thursday.querySelector('[data-testid="month-dots"]')!.children.length).toBe(4)
    expect(all('month-day-row').map((row) => row.textContent)).toContain('16:30Zahnarzt Ben')
    await click(all('month-day').find((day) => day.dataset.date === '2026-11-29')!.querySelector('button'))
    expect(all('month-day-row').map((row) => row.textContent)).toEqual(['15:00Kaffee bei Oma'])
  })
})

describe('TIME-12 a phone', () => {
  it('TIME-12 EC-3 the week is three days from the chosen one, with "Heute" and the line of now; stepping moves three', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    await open('/week')
    expect(all('week-day').map((day) => day.lastElementChild!.textContent)).toEqual(['26', '27', '28'])
    expect(text('period')).toBe('26.–28. Nov.')
    expect(text('week-number')).toBe('KW 48')
    expect(one('now-line')).not.toBeNull()
    expect(one('to-today')).not.toBeNull()
    await click(one('step-on'))
    expect(text('period')).toBe('29. Nov. – 1. Dez.')
    expect(one('step-on')!.getAttribute('aria-label')).toBe('Nächste drei Tage')
  })
})

describe('TIME-12 the jump to a calendar week', () => {
  it('TIME-12 AC-13 the button "KW 48" opens the jump filled in; the week shows the week jumped to', async () => {
    await open('/week')
    await click(one('week-button'))
    expect((one('jump-week') as HTMLInputElement).value).toBe('48')
    expect((one('jump-year') as HTMLInputElement).value).toBe('2026')
    await jump('2', '2027')
    expect(text('period')).toBe('11.–17. Januar 2027')
  })

  it('TIME-12 AC-13 with week numbers off the button says "Zu KW …"; K jumps from the day into the week, the month to its month', async () => {
    await open('/day', as({ week_numbers: false }))
    expect(text('week-button')).toBe('Zu KW …')
    await press('k')
    await jump('2', '2027')
    expect(one('view-week')).not.toBeNull()
    expect(text('period')).toBe('11.–17. Januar 2027')
    await press('m')
    await press('k')
    await jump('9', '2027')
    expect(one('view-month')).not.toBeNull()
    expect(text('period')).toBe('März 2027')
  })

  it('TIME-12 AC-15 EC-15 a week the year does not have, or no number, says so and stays', async () => {
    await open('/week')
    await click(one('week-button'))
    for (const [week, year, said] of [
      ['0', '2026', '2026 hat 53 Kalenderwochen, bitte 1 bis 53.'],
      ['54', '2026', '2026 hat 53 Kalenderwochen, bitte 1 bis 53.'],
      ['53', '2027', '2027 hat 52 Kalenderwochen, bitte 1 bis 52.'],
      ['zwei', '2027', '2027 hat 52 Kalenderwochen, bitte 1 bis 52.'],
    ]) {
      await jump(week, year)
      expect(text('jump-problem')).toBe(said)
      expect(text('period')).toBe('23.–29. November 2026')
    }
  })

  it('TIME-12 EC-15 a year that is no four-digit number says so and stays', async () => {
    await open('/week')
    await click(one('week-button'))
    for (const year of ['zwanzig', '27', '']) {
      await jump('2', year)
      expect(text('jump-problem')).toBe('Bitte ein Jahr mit vier Ziffern eingeben.')
      expect(text('period')).toBe('23.–29. November 2026')
    }
  })

  it('TIME-12 EC-16 KW 53 2026 and KW 1 2027 show exactly their ISO weeks over the turn of the year', async () => {
    await open('/week')
    await click(one('week-button'))
    await jump('53', '2026')
    expect(text('period')).toBe('28. Dezember 2026 – 3. Januar 2027')
    expect(text('week-number')).toBe('KW 53')
    await click(one('week-button'))
    await jump('1', '2027')
    expect(text('period')).toBe('4.–10. Januar 2027')
  })

  it('TIME-12 EC-17 with Sunday first the jump shows the days holding the Monday of the week', async () => {
    await open('/week', as({ week_start: 0 }))
    await click(one('week-button'))
    await jump('2', '2027')
    expect(text('period')).toBe('10.–16. Januar 2027')
    expect(text('week-number')).toBe('KW 2')
  })
})

describe('TIME-12 the reckoning behind the views', () => {
  it('TIME-12 AC-15 EC-16 ISO weeks: the week with the first Thursday is week 1', () => {
    expect(isoWeekOf('2026-11-26')).toEqual({ week: 48, year: 2026 })
    expect(isoWeekOf('2027-01-01')).toEqual({ week: 53, year: 2026 })
    expect(isoWeekOf('2027-01-04')).toEqual({ week: 1, year: 2027 })
    expect(isoWeekOf('2024-12-30')).toEqual({ week: 1, year: 2025 })
    expect(mondayOfWeek(53, 2026)).toBe('2026-12-28')
    expect(mondayOfWeek(1, 2027)).toBe('2027-01-04')
    expect([2015, 2020, 2026, 2027, 2028].map(weeksInYear)).toEqual([53, 53, 53, 52, 52])
    expect(monthWeeks('2026-11-10', 1)).toHaveLength(6)
    expect(monthWeeks('2027-02-10', 1)).toHaveLength(4)
  })

  it('TIME-12 AC-2 AC-4 quarters, columns and moves on the wall clock', () => {
    expect([0.1, 0.13, 0.38, 0.9].map(quarter)).toEqual([0, 0.25, 0.5, 1])
    const rows = ['A', 'B', 'C', 'D'].map((name) => at(1, name, '2026-11-26 10:00', '2026-11-26 11:00'))
    const laid = columns(timedParts(rows, ['2026-11-26'], 'Europe/Berlin').get('2026-11-26')!)
    expect(laid.placed.map((entry) => [entry.column, entry.columns])).toEqual([[0, 3], [1, 3], [2, 3]])
    expect(laid.more[0].parts.map((part) => part.row.title)).toEqual(['D'])
    // A day later across the change of the clocks: 18:30 stays 18:30.
    const saturday = at(1, 'Training', '2027-03-27 18:30', '2027-03-27 20:00')
    expect(moved(saturday, 'Europe/Berlin', 0, 1, false)).toEqual({ start: '2027-03-28T18:30', end: '2027-03-28T20:00', time_zone: 'Europe/Berlin' })
    expect(moved(saturday, 'Europe/Berlin', -120, 0, true)).toEqual({ start: '2027-03-27T18:30', end: '2027-03-27T18:45', time_zone: 'Europe/Berlin' })
  })
})
