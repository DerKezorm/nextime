/** TIME-13 and TIME-12, the column beside Month, Week and Day (decided while testing, 2026-11-23): while the window is
 * wide, the events of what is shown stand beside the view: one calendar alone, a collection or "Alles"; coming by month,
 * a series once with its rule, the past folded; each date sheet in its calendar's color; a click opens the event and
 * marks the row; folded away and back, kept on the device. Not narrower. */
import { act } from 'react'

import App from './App'
import i18n from './i18n'
import type { Collection } from './features/calendars/api'
import { announce } from './features/calendars/store'
import { forgetPrefs, setPref } from './lib/devicePref'
import { setViewDay } from './features/views/state'
import { AuthProvider } from './state/auth'
import { calls, click, render, settle, start, stop } from './testing'
import { NOW, theWeek, views } from './viewFakes'

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
  vi.unstubAllGlobals()
})

const item = (id: number, title: string, start: string, more: Record<string, unknown> = {}) => ({
  id, occurrence: null, calendar_id: 7, title, all_day: true, start, end: start, time_zone: '', recurring: false, coming: true, location: '', busy_only: false, rrule: '', ...more,
})
const COMING = [
  item(1, 'Ben', '2026-11-30'),
  item(2, 'Oma', '2026-12-09', { recurring: true, rrule: 'FREQ=YEARLY', occurrence: '20261209', first: '1954-12-09' }),
  item(3, 'Zahnarzt', '2026-12-15T09:00:00Z', { all_day: false, end: '2026-12-15T10:00:00Z', time_zone: 'Europe/Berlin', location: 'Lindenstraße 4' }),
]
const PAST = [item(4, 'Vorbei', '2026-10-01', { coming: false })]

function wide(on: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: on && query.includes('1200'), addEventListener: () => undefined, removeEventListener: () => undefined }))
}

async function open(items = [...COMING, ...PAST], where = '/month', collections: Collection[] = []): Promise<void> {
  views({
    rows: theWeek(),
    collections,
    more: (_method, path) => {
      if (!path.startsWith('/api/search/calendar/7') && !path.startsWith('/api/search/upcoming')) return undefined
      const query = new URL(path, 'http://x').searchParams
      const offset = Number(query.get('offset') ?? 0)
      const limit = Number(query.get('limit') ?? 50)
      const coming = items.filter((entry) => entry.coming).length
      return { status: 200, body: { total: items.length, future_total: coming, offset, items: items.slice(offset, offset + limit) } }
    },
  })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]

describe('TIME-13 calendar list column', () => {
  it('TIME-13 calendar list column the calendar shown alone, its number, "Demnächst" then months, one compact row each', async () => {
    wide(true)
    setPref('only', 1, '7')
    await open()
    const column = one('calendar-column')!
    expect(column.textContent).toContain('Verein')
    expect(column.textContent).not.toContain('Kommend')
    expect(one('column-count')?.textContent).toBe('4')
    expect(all('column-month').map((node) => node.textContent)).toEqual(['Demnächst', 'Dezember 2026'])
    expect(all('column-month')[0].className).toContain('sticky')
    expect(all('column-title').map((node) => node.textContent)).toEqual(['Ben', 'Oma', 'Zahnarzt'])
    const subs = all('column-sub').map((node) => node.textContent)
    expect(subs).toEqual(['in 4 Tagen', 'in 13 Tagen · jährlich seit 1954', 'in 19 Tagen · 10:00'])
    // Two lines, never a third: no "Jedes Jahr" line, no place line.
    expect(all('column-row')[1].querySelectorAll(':scope > span:last-child > span')).toHaveLength(2)
    expect(column.textContent).not.toContain('Lindenstraße 4')
    // The date as a small calendar sheet: two rings, the weekday on a head in the calendar's color, the day below.
    const date = all('column-date')[1]
    expect(date.textContent).toBe('Mi9')
    expect(date.children).toHaveLength(4)
    expect((date.children[2] as HTMLElement).style.color).toBe('var(--color-cal-green)')
    expect(one('view-month')).not.toBeNull()
  })

  it('TIME-13 calendar list column "seit" only for a first year before this one', async () => {
    wide(true)
    setPref('only', 1, '7')
    await open([
      item(8, 'Neu', '2027-01-15', { recurring: true, rrule: 'FREQ=YEARLY', occurrence: '20270115', first: '2026-01-15' }),
      item(9, 'Alt', '2027-02-01', { recurring: true, rrule: 'FREQ=YEARLY', occurrence: '20270201', first: '2025-02-01' }),
    ])
    const subs = all('column-sub').map((node) => node.textContent)
    expect(subs[0]).not.toContain('seit')
    expect(subs[1]).toContain('jährlich seit 2025')
  })

  it('TIME-13 calendar list column today and tomorrow in the accent, a stretch of days with its end', async () => {
    wide(true)
    setPref('only', 1, '7')
    await open([item(5, 'Heute', '2026-11-26'), item(6, 'Morgen', '2026-11-27T13:00:00Z', { all_day: false, end: '2026-11-27T14:00:00Z' }), item(7, 'Urlaub', '2026-12-20', { end: '2026-12-24' })])
    const distances = all('column-distance')
    expect(distances.map((node) => node.textContent)).toEqual(['heute', 'morgen', 'in 24 Tagen'])
    expect(distances[0].className).toContain('text-accent-400')
    expect(distances[2].className).not.toContain('text-accent-400')
    expect(all('column-sub')[1].textContent).toBe('morgen · 14:00')
    expect(all('column-sub')[2].textContent).toBe('in 24 Tagen · bis 24. Dezember')
  })

  it('TIME-13 calendar list column a click opens the event and marks its row; the past comes unfolded on asking', async () => {
    wide(true)
    setPref('only', 1, '7')
    await open()
    await click(all('column-row')[1])
    expect(one('event-panel')).not.toBeNull()
    expect(all('column-row')[1].getAttribute('data-chosen')).toBe('true')
    expect(all('column-row')[1].className).toContain('border-accent-500')
    await click(one('panel-close'))
    expect(one('column-past-list')).toBeNull()
    expect(one('column-past')?.textContent).toContain('Vergangen (1)')
    await click(one('column-past'))
    await settle()
    expect(one('column-past-list')?.textContent).toContain('Vorbei')
    expect(calls.some((call) => call.path.includes('/api/search/calendar/7') && call.path.includes('offset=3'))).toBe(true)
  })

  it('TIME-13 calendar list column empty: says so and offers a new event', async () => {
    wide(true)
    setPref('only', 1, '7')
    await open([])
    expect(one('column-empty')?.textContent).toContain('Noch keine Termine in Verein.')
    await click(one('column-new'))
    expect((one('field-calendar') as HTMLSelectElement).value).toBe('7')
  })

  it('TIME-13 calendar list column none narrower', async () => {
    wide(false)
    setPref('only', 1, '7')
    await open()
    expect(one('calendar-column')).toBeNull()
    expect(one('view-month')).not.toBeNull()
  })

  it('TIME-13 calendar list column "Alles": every calendar shown, "Kommende Termine" with the number, each sheet in the color of its calendar', async () => {
    wide(true)
    await open([item(1, 'Elternabend', '2026-11-30', { calendar_id: 1 }), item(2, 'Planung', '2026-12-01', { calendar_id: 3 }), item(3, 'Training', '2026-12-02', { calendar_id: 7 })])
    expect(one('column-heading')?.textContent).toBe('Kommende Termine')
    expect(one('column-count')?.textContent).toBe('3')
    const colors = all('column-date').map((date) => (date.children[2] as HTMLElement).style.color)
    expect(colors).toEqual(['var(--color-cal-slate)', 'var(--color-cal-teal)', 'var(--color-cal-green)'])
    const asked = calls.find((call) => call.path.startsWith('/api/search/upcoming'))!
    expect(new URL(asked.path, 'http://x').searchParams.has('calendars')).toBe(false)
  })

  it('TIME-13 calendar list column a collection: its name, only its calendars asked', async () => {
    wide(true)
    setPref('collection', 1, '9')
    await open([item(1, 'Elternabend', '2026-11-30', { calendar_id: 1 })], '/month', [{ id: 9, name: 'Familie und Verein', calendar_ids: [1, 7], updated_at: '' }])
    expect(one('column-heading')?.textContent).toBe('Familie und Verein')
    const asked = calls.find((call) => call.path.startsWith('/api/search/upcoming'))!
    expect(new URL(asked.path, 'http://x').searchParams.get('calendars')).toBe('1,7')
    expect(calls.some((call) => call.path.startsWith('/api/search/calendar/'))).toBe(false)
  })

  it('TIME-12 column beside the week and the day as beside the month', async () => {
    wide(true)
    await open(COMING, '/week')
    expect(one('view-week')).not.toBeNull()
    expect(one('column-heading')?.textContent).toBe('Kommende Termine')
    expect(all('column-title').map((node) => node.textContent)).toEqual(['Ben', 'Oma', 'Zahnarzt'])
    stop()
    start()
    wide(true)
    await open(COMING, '/day')
    expect(one('view-day')).not.toBeNull()
    expect(one('calendar-column')).not.toBeNull()
  })

  it('TIME-12 column folded away and back, kept on the device', async () => {
    wide(true)
    await open(COMING, '/week')
    await click(one('column-fold'))
    expect(one('calendar-column')).toBeNull()
    expect(one('column-unfold')).not.toBeNull()
    stop()
    start()
    wide(true)
    await open(COMING, '/month')
    expect(one('calendar-column')).toBeNull()
    await click(one('column-unfold'))
    expect(one('calendar-column')).not.toBeNull()
    expect(one('column-fold')?.getAttribute('aria-label')).toBe('Liste einklappen')
  })
})

describe('TIME-13 calendar list column after the review', () => {
  it('TIME-13 calendar list column more on scrolling, and a change heard keeps what was loaded', async () => {
    wide(true)
    const many = Array.from({ length: 70 }, (_, index) => item(100 + index, `Termin ${index}`, `2027-01-${String((index % 28) + 1).padStart(2, '0')}`))
    await open(many)
    expect(all('column-row')).toHaveLength(50)
    await act(async () => void one('calendar-column')!.dispatchEvent(new Event('scroll')))
    await settle()
    expect(all('column-row')).toHaveLength(70)
    await act(async () => announce())
    await settle()
    expect(all('column-row')).toHaveLength(70)
    const last = calls.filter((call) => call.path.startsWith('/api/search/upcoming')).at(-1)!
    expect(new URL(last.path, 'http://x').searchParams.get('limit')).toBe('70')
  })
})
