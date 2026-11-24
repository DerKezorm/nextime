/** TIME-12 AC-16 to AC-22 in the interface: the year as a heat map (the same as TIMEIOS-8 in the app). The switch and
 * the key J, the head with the year, the months from the first weekday set, the four steps and the legend, today and
 * the holiday dot, the jumps into Day and Month, the labels and keys for a screen reader and the keyboard, what is
 * asked of the server, changes from elsewhere, a phone, no connection, and the colors of the style sheet. */
import { act } from 'react'

import App from './App'
import { announce } from './features/calendars/store'
import type { YearMap } from './features/calendars/api'
import { markUnreachable, resetConnection } from './features/offline/connection'
import { setViewDay } from './features/views/state'
import { addYears, heatStep } from './features/views/year'
import i18n from './i18n'
import { forgetPrefs, prefKey } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, me, render, settle, start, stop } from './testing'
import { BERLIN, NOW, views, type Fake } from './viewFakes'

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
  resetConnection()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

/** The counts the server gives for a year (by default the same for every year). */
let counts: (year: number) => YearMap = (year) => ({ year, zone: 'Europe/Berlin', days: {}, holidays: [] })

async function open(where: string, change: Partial<Fake> = {}, display: Partial<typeof BERLIN> = {}): Promise<void> {
  views({
    rows: [],
    ...change,
    more: (method, path) => {
      if (path === '/api/auth/me') return { status: 200, body: me({ id: 1, display: { ...BERLIN, ...display } }) }
      if (method === 'GET' && path.startsWith('/api/events/year?')) {
        const year = Number(new URL(path, 'http://x').searchParams.get('year'))
        return { status: 200, body: counts(year) }
      }
      return undefined
    },
  })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
  await settle()
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string, scope: ParentNode = box) => [...scope.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const field = (date: string) => box.querySelector<HTMLButtonElement>(`.nt-heat[data-date="${date}"]`)!
const asked = () => calls.filter((call) => call.method === 'GET' && call.path.startsWith('/api/events/year?')).map((call) => new URL(call.path, 'http://x').searchParams)
const press = async (key: string, target: EventTarget = window) =>
  act(async () => void target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })))
const monthNamed = (name: string) => all('year-month').find((month) => month.getAttribute('aria-label') === name)!
/** The places before the 1st in a month's grid of days. */
const blanks = (month: HTMLElement) => {
  const days = month.querySelector('.nt-heat')!.parentElement!
  return [...days.children].indexOf(days.querySelector('.nt-heat')!)
}

describe('TIME-12 the year as a heat map', () => {
  it('TIME-12 AC-16 "Jahr" is the fourth entry of the switch and the key J; the head names the year, ‹ › and "Heute" page', async () => {
    counts = (year) => ({ year, zone: 'Europe/Berlin', days: {}, holidays: [] })
    await open('/month')
    const names = [...one('view-switch')!.querySelectorAll('button')].map((button) => button.textContent)
    expect(names).toEqual(['Tag', 'Woche', 'Monat', 'Jahr'])
    expect(one('view-switch')!.querySelectorAll('button')[3].getAttribute('title')).toBe('Jahr (Taste J)')
    await click([...one('view-switch')!.querySelectorAll('button')][3])
    await settle()
    expect(one('view-year')).not.toBeNull()
    expect(one('period')!.textContent).toBe('2026')
    expect(all('year-month')).toHaveLength(12)
    expect(all('year-month-name').map((name) => name.textContent)).toEqual(['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'])
    // No calendar week here: the year has none to name.
    expect(one('week-button')).toBeNull()
    await click(one('step-back'))
    await settle()
    expect(one('period')!.textContent).toBe('2025')
    expect(asked().at(-1)!.get('year')).toBe('2025')
    await click(one('to-today'))
    await settle()
    expect(one('period')!.textContent).toBe('2026')
    // The arrows of the page step a year too.
    await press('ArrowRight')
    await settle()
    expect(one('period')!.textContent).toBe('2027')
    // J from another view.
    await press('w')
    await settle()
    expect(one('view-week')).not.toBeNull()
    await press('j')
    await settle()
    expect(one('view-year')).not.toBeNull()
  })

  it('TIME-12 AC-16 the sidebar has "Heute" and "Kalender"; "Kalender" comes back to the view shown last, the switch above chooses it', async () => {
    await open('/')
    const places = () => [...one('sidebar-places')!.querySelectorAll<HTMLAnchorElement>('[data-testid="nav-today"], [data-testid="nav-calendar"]')]
    expect(places().map((link) => link.textContent)).toEqual(['Heute', 'Kalender'])
    expect(one('nav-today')!.className).toContain('nt-chosen')
    // The month from the start.
    expect(one('nav-calendar')!.getAttribute('href')).toBe('/month')
    await click(one('nav-calendar'))
    await settle()
    expect(one('view-month')).not.toBeNull()
    expect(one('nav-calendar')!.className).toContain('nt-chosen')
    expect(one('nav-today')!.className).not.toContain('nt-chosen')
    // The switch above chooses the span; the sidebar stays on "Kalender", for Day too.
    await click([...one('view-switch')!.querySelectorAll('button')][0])
    await settle()
    expect(one('view-day')).not.toBeNull()
    expect(one('nav-calendar')!.className).toContain('nt-chosen')
    await click([...one('view-switch')!.querySelectorAll('button')][3])
    await settle()
    expect(one('to-today')!.getAttribute('title')).toBe('Zu heute springen')
    await click(one('nav-today'))
    await settle()
    expect(one('view-today')).not.toBeNull()
    await click(one('nav-calendar'))
    await settle()
    expect(one('view-year')).not.toBeNull()
  })

  it('TIME-12 EC-19 the weeks begin on the first weekday set; a leap year has a 29th of February', async () => {
    await open('/year')
    // 1 January 2026 is a Thursday: three places before it from Monday.
    expect(blanks(monthNamed('Januar 2026'))).toBe(3)
    expect(monthNamed('Februar 2026').querySelectorAll('.nt-heat')).toHaveLength(28)
    stop()
    start()
    setViewDay('2028-02-10')
    await open('/year', {}, { week_start: 0 })
    // From Sunday: four places before Thursday the 1st of January 2026 … and 2028 starts on a Saturday.
    expect(one('period')!.textContent).toBe('2028')
    expect(blanks(monthNamed('Januar 2028'))).toBe(6)
    expect(monthNamed('Februar 2028').querySelectorAll('.nt-heat')).toHaveLength(29)
  })

  it('TIME-12 AC-19 four fixed steps and the legend; EC-18 a year without events is free', async () => {
    counts = (year) => ({ year, zone: 'Europe/Berlin', days: (year === 2026 ? { '2026-03-03': 1, '2026-03-04': 3, '2026-03-05': 5, '2026-03-06': 6, '2026-03-07': 2, '2026-03-08': 4, '2026-03-09': 40 } : {}) as Record<string, number>, holidays: [] })
    await open('/year')
    const steps = ['03', '04', '05', '06', '07', '08', '09', '10'].map((day) => field(`2026-03-${day}`).dataset.step)
    expect(steps).toEqual(['1', '2', '3', '4', '2', '3', '4', '0'])
    expect([0, 1, 2, 3, 4, 5, 6, 99].map(heatStep)).toEqual([0, 1, 2, 2, 3, 3, 4, 4])
    expect([...one('year-legend')!.querySelectorAll('li')].map((item) => item.textContent)).toEqual(['frei', '1', '2–3', '4–5', '6+', 'Feiertag'])
    await click(one('step-on'))
    await settle()
    expect(one('period')!.textContent).toBe('2027')
    expect([...box.querySelectorAll<HTMLElement>('.nt-heat')].every((day) => day.dataset.step === '0')).toBe(true)
    await click(field('2027-06-15'))
    await settle()
    expect(one('view-day')).not.toBeNull()
  })

  it('TIME-12 AC-20 today has the ring, a holiday the dot over its step, and the weekend is not tinted', async () => {
    counts = (year) => ({ year, zone: 'Europe/Berlin', days: { '2026-12-25': 2 }, holidays: ['2026-12-25', '2026-12-26'] })
    await open('/year')
    expect(field('2026-11-26').dataset.today).toBe('true')
    expect(field('2026-11-26').getAttribute('aria-current')).toBe('date')
    expect(box.querySelectorAll('.nt-heat[data-today]')).toHaveLength(1)
    expect(field('2026-12-25').dataset.holiday).toBe('true')
    expect(field('2026-12-25').dataset.step).toBe('2')
    expect(field('2026-12-26').dataset.holiday).toBe('true')
    expect(field('2026-12-26').dataset.step).toBe('0')
    expect(one('view-year')!.querySelector('.nt-weekend')).toBeNull()
  })

  it('TIME-12 AC-21 a day opens Day, a month\'s name opens Month', async () => {
    await open('/year')
    await click(field('2026-03-03'))
    await settle()
    expect(one('view-day')).not.toBeNull()
    expect(one('period')!.textContent).toContain('3. März 2026')
    await press('j')
    await settle()
    await click(all('year-month-name')[6])
    await settle()
    expect(one('view-month')).not.toBeNull()
    expect(one('period')!.textContent).toBe('Juli 2026')
  })

  it('TIME-12 AC-22 a screen reader hears date and fullness, the months are groups; the arrows walk the days, one Tab stop', async () => {
    counts = (year) => ({ year, zone: 'Europe/Berlin', days: { '2026-03-03': 4, '2026-03-04': 1 }, holidays: ['2026-01-01'] })
    await open('/year')
    expect(field('2026-03-03').getAttribute('aria-label')).toBe('Dienstag, 3. März, 4 Termine')
    expect(field('2026-03-04').getAttribute('aria-label')).toBe('Mittwoch, 4. März, 1 Termin')
    expect(field('2026-03-05').getAttribute('aria-label')).toBe('Donnerstag, 5. März, frei')
    expect(field('2026-01-01').getAttribute('aria-label')).toBe('Donnerstag, 1. Januar, frei, Feiertag')
    expect(all('year-month').map((month) => [month.getAttribute('role'), month.getAttribute('aria-label')])[2]).toEqual(['group', 'März 2026'])
    expect(all('year-month-name')[2].getAttribute('aria-label')).toBe('März 2026 als Monat zeigen')
    // One Tab stop among the days: the day shown (today).
    const stops = () => [...box.querySelectorAll<HTMLButtonElement>('.nt-heat')].filter((day) => day.tabIndex === 0).map((day) => day.dataset.date)
    expect(stops()).toEqual(['2026-11-26'])
    field('2026-11-26').focus()
    await press('ArrowRight', document.activeElement!)
    expect((document.activeElement as HTMLElement).dataset.date).toBe('2026-11-27')
    await press('ArrowDown', document.activeElement!)
    expect((document.activeElement as HTMLElement).dataset.date).toBe('2026-12-04')
    await press('ArrowUp', document.activeElement!)
    await press('ArrowLeft', document.activeElement!)
    expect((document.activeElement as HTMLElement).dataset.date).toBe('2026-11-26')
    expect(stops()).toEqual(['2026-11-26'])
    // The arrows among the days do not page the year, and stop at its end.
    expect(one('period')!.textContent).toBe('2026')
    field('2026-12-31').focus()
    await press('ArrowRight', document.activeElement!)
    expect((document.activeElement as HTMLElement).dataset.date).toBe('2026-12-31')
    expect(one('period')!.textContent).toBe('2026')
  })

  it('TIME-12 AC-18 the year asks for what the views show: the zone, the collection, one hidden calendar alone', async () => {
    await open('/year')
    expect(asked().at(-1)!.get('zone')).toBe('Europe/Berlin')
    expect(asked().at(-1)!.get('calendars')).toBeNull()
    stop()
    start()
    localStorage.setItem(prefKey('collection', 1), '9')
    forgetPrefs()
    await open('/year', { collections: [{ id: 9, name: 'Familie', calendar_ids: [1, 2], updated_at: '2026-11-20T10:00:00Z' }] })
    expect(asked().at(-1)!.get('calendars')).toBe('1,2')
    expect(asked().at(-1)!.get('hidden')).toBeNull()
    stop()
    start()
    localStorage.setItem(prefKey('only', 1), '3')
    forgetPrefs()
    await open('/year')
    expect(asked().at(-1)!.get('calendars')).toBe('3')
    expect(asked().at(-1)!.get('hidden')).toBe('true')
  })

  it('TIME-12 EC-21 a change from elsewhere shows without reloading', async () => {
    let full = 1
    counts = (year) => ({ year, zone: 'Europe/Berlin', days: { '2026-05-04': full }, holidays: [] })
    await open('/year')
    expect(field('2026-05-04').dataset.step).toBe('1')
    full = 6
    await act(async () => announce())
    await settle()
    expect(field('2026-05-04').dataset.step).toBe('4')
  })

  it('TIME-12 EC-22 on a phone the months stand two by two and the month leads to the year', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    await open('/month')
    await click(one('to-year'))
    await settle()
    expect(one('view-year')).not.toBeNull()
    expect(one('year-grid')!.className).toContain('grid-cols-2')
    expect(one('view-switch')).toBeNull()
  })

  it('TIME-12 EC-23 without a connection the year says it shows once connected', async () => {
    await open('/year')
    const before = asked().length
    await act(async () => markUnreachable())
    await settle()
    expect(one('offline-outside')).not.toBeNull()
    expect(one('year-grid')).toBeNull()
    expect(asked().length).toBe(before)
  })

  it('TIME-12 AC-19 AC-20 the colors of the steps, the ring and the dot, light and dark', () => {
    const css = readFileSync('src/styles/index.css', 'utf-8')
    const values = (shade: string) => ['0', '1', '2', '3', '4'].map((step) => new RegExp(`--color-heat-${step}: (#[0-9a-f]{6});`, 'g').exec(shade)?.[1])
    const [dark, light] = css.split(":root[data-theme='light'] {")
    expect(values(dark)).toEqual(['#22222b', '#2c3446', '#3f4f6e', '#6880a8', '#9fb4d8'])
    expect(values(css.slice(css.indexOf('--color-heat-4: #9fb4d8;') + 1))).toEqual(['#ececf1', '#dbe2ee', '#b3c2db', '#7f95bb', '#4a6491'])
    expect(light).toBeDefined()
    expect(css).toMatch(/\.nt-heat,\n\.nt-heat-swatch \{[^}]*border-radius: 4px;/)
    expect(css).toMatch(/\.nt-heat\[data-today\] \{\n {2}box-shadow: inset 0 0 0 2px var\(--color-mist-100\);/)
    expect(css).toMatch(/\.nt-heat\[data-holiday\]::after,[^{]*\{[^}]*width: 5px;\n {2}height: 5px;[^}]*background-color: var\(--color-bad-500\);/)
  })

  it('TIME-12 AC-16 a year on keeps the day, the 29th of February becomes the 28th', () => {
    expect(addYears('2026-03-03', 1)).toBe('2027-03-03')
    expect(addYears('2028-02-29', 1)).toBe('2029-02-28')
    expect(addYears('2028-02-29', -4)).toBe('2024-02-29')
  })
})
