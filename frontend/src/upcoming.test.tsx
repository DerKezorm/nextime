/** "Demnächst" as weeks (asked for while testing, 2026-11-24, the concept "Wochen" chosen): "Als Nächstes" with what
 * follows, the strip of the next 14 days with the calm stretch, this week, the next, the one after and "Später" with
 * whole days only, free days between (an event over several days keeps its days busy), "+ n weitere", and the chips
 * setting the same choice as "Sammlung" and "Nur diesen". Today is Thursday 26 November 2026, 16:05 in Berlin
 * (`viewFakes`), the week begins on Monday. */
import App from './App'
import { calmStretch, columnsFrom, freeRuns } from './features/upcoming/plan'
import { setViewDay } from './features/views/state'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, render, settle, start, stop } from './testing'
import { at, NOW, views, type Fake } from './viewFakes'

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

const ROWS = () => [
  at(3, 'Planung', '2026-11-26 09:00', '2026-11-26 10:00'),
  at(2, 'Zahnarzt', '2026-11-26 18:00', '2026-11-26 19:00'),
  at(2, 'Kino', '2026-11-26 20:00', '2026-11-26 22:00'),
  at(7, 'Training', '2026-11-27 18:30', '2026-11-27 20:00'),
  at(2, 'Urlaub', '2026-12-01', '2026-12-03'),
  at(6, 'Gelbe Tonne', '2026-12-01', '2026-12-01'),
  at(1, 'Weihnachtsfeier', '2026-12-18 19:00', '2026-12-18 23:00'),
  at(2, 'Heiligabend bei Oma', '2026-12-24', '2026-12-24'),
  at(5, '1. Weihnachtstag', '2026-12-25', '2026-12-25'),
]

async function open(fake: Partial<Fake> = {}): Promise<void> {
  views({ rows: ROWS(), ...fake })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    '/upcoming',
  )
  await settle()
}

const one = (id: string, scope: ParentNode = document) => scope.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string, scope: ParentNode = box) => [...scope.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const week = (kind: string) => box.querySelector<HTMLElement>(`[data-testid="upcoming-week"][data-kind="${kind}"]`)!
const windows = () => calls.filter((call) => call.method === 'GET' && call.path.startsWith('/api/events?')).map((call) => new URL(call.path, 'http://x').searchParams)

describe('Demnächst as weeks', () => {
  it('stands in the sidebar below "Kalender"', async () => {
    await open()
    expect([...one('sidebar-places')!.querySelectorAll('[data-testid^="nav-"]')].map((link) => link.textContent)).toEqual(['Heute', 'Kalender', 'Demnächst'])
    expect(one('nav-upcoming')!.className).toContain('nt-chosen')
  })

  it('"Als Nächstes" names what comes next, how soon, when and where, and what follows that day', async () => {
    await open()
    const card = one('upcoming-next')!
    expect(card.textContent).toContain('Als Nächstes · in 1 Std 55 Min')
    expect(card.textContent).toContain('Zahnarzt')
    expect(card.textContent).toContain('18:00–19:00 · Persönlich')
    expect(card.textContent).toContain('Danach: Kino um 20:00')
    await click(card)
    expect(one('event-panel')).not.toBeNull()
  })

  it('"Als Nächstes": today\'s whole day does not push away what still comes today with a time', async () => {
    await open({ rows: [...ROWS(), at(2, 'Gelber Sack', '2026-11-26', '2026-11-26')] })
    expect(one('upcoming-next')!.textContent).toContain('Zahnarzt')
  })

  it('"Als Nächstes" counts whole days too: tomorrow\'s birthday before tomorrow\'s evening', async () => {
    // 22:40, today is over.
    vi.setSystemTime(new Date('2026-11-26T21:40:00Z'))
    await open({ rows: [...ROWS(), at(2, 'Geburtstag Oma', '2026-11-27', '2026-11-27')] })
    const card = one('upcoming-next')!
    expect(card.textContent).toContain('Als Nächstes · morgen')
    expect(card.textContent).toContain('Geburtstag Oma')
    expect(card.textContent).toContain('ganztägig · Persönlich')
    expect(card.textContent).toContain('Danach: Training um 18:30')
  })

  it('the strip shows the next 14 days by fullness and names the calm stretch', async () => {
    await open()
    const bars = all('upcoming-bar')
    expect(bars).toHaveLength(14)
    // Today three events (2–3), tomorrow one, the holiday over 1 to 3 December on each of its days.
    expect(bars.slice(0, 8).map((bar) => bar.dataset.step)).toEqual(['2', '1', '0', '0', '0', '1', '1', '1'])
    expect(bars[0].getAttribute('aria-label')).toBe('Donnerstag, 26. November, 3 Termine')
    expect(one('upcoming-calm')!.textContent).toBe('Ruhe: Sa 28. bis Mo 30. frei')
  })

  it('the weeks stand as columns with their days, free days between and "Später" with whole days only', async () => {
    await open()
    expect(all('upcoming-week').map((column) => column.dataset.kind)).toEqual(['this', 'next', 'week', 'later'])
    expect(week('this').textContent).toContain('Diese Woche')
    expect(week('this').textContent).toContain('KW 48 · 4 Termine')
    expect(all('upcoming-day', week('this')).map((day) => day.dataset.date)).toEqual(['2026-11-26', '2026-11-27'])
    expect(all('upcoming-gap', week('this')).map((gap) => gap.textContent)).toEqual(['Sa 28. bis So 29. · 2 Tage frei'])
    // The holiday keeps 2 and 3 December busy: free only Monday before and 4 to 6 after.
    expect(week('next').textContent).toContain('KW 49 · 1 Termin')
    expect(all('upcoming-gap', week('next')).map((gap) => gap.textContent)).toEqual(['Mo 30. · frei', 'Fr 4. bis So 6. · 3 Tage frei'])
    const first = all('upcoming-day', week('next'))[0]
    expect(first.textContent).toContain('Gelbe Tonne')
    expect(first.textContent).toContain('bis 3. Dez.')
    expect(week('week').textContent).toContain('KW 50')
    expect(week('week').textContent).toContain('Die ganze Woche frei.')
    // Later: whole days and the background, the evening of 18 December only counted.
    expect(all('upcoming-event', week('later')).map((event) => event.textContent)).toEqual(['ganzt.Heiligabend bei Oma'])
    expect(week('later').textContent).toContain('1. Weihnachtstag')
    expect(one('upcoming-later-more', week('later'))!.textContent).toBe('und 1 weiterer Termin im Kalender')
    // From the column's start to four weeks after the third: one question for it all.
    const asked = windows().at(-1)!
    expect(Date.parse(asked.get('end')!) - Date.parse(asked.get('start')!)).toBeGreaterThan(40 * 86_400_000)
  })

  it('a long week shows eight rows and "+ n weitere", which opens it', async () => {
    const many = [9, 10, 11, 12, 13, 14, 15].map((hour) => at(3, `Termin ${hour}`, `2026-11-27 ${hour}:00`, `2026-11-27 ${hour}:30`))
    await open({ rows: [...ROWS(), ...many] })
    expect(all('upcoming-event', week('this'))).toHaveLength(8)
    expect(one('upcoming-more', week('this'))!.textContent).toBe('+ 3 weitere')
    await click(one('upcoming-more', week('this')))
    expect(all('upcoming-event', week('this'))).toHaveLength(11)
    expect(one('upcoming-more', week('this'))).toBeNull()
  })

  it('four weeks side by side stand equally high to the bottom and show all their rows, no column scrolling on its own', async () => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    })
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1600)
    const many = [9, 10, 11, 12, 13, 14, 15].map((hour) => at(3, `Termin ${hour}`, `2026-11-27 ${hour}:00`, `2026-11-27 ${hour}:30`))
    await open({ rows: [...ROWS(), ...many] })
    expect(one('view-upcoming')!.dataset.tall).toBe('true')
    expect(all('upcoming-event', week('this'))).toHaveLength(11)
    expect(one('upcoming-more', week('this'))).toBeNull()
    expect(week('this').className).not.toContain('overflow')
    expect(one('upcoming-weeks')!.className).toContain('@[72rem]:flex-1')
    expect(one('upcoming-weeks')!.className).toContain('@[72rem]:items-stretch')
    width.mockRestore()
    vi.unstubAllGlobals()
  })

  it('the chips set the same choice as "Sammlung" and "Nur diesen"', async () => {
    await open({ collections: [{ id: 9, name: 'Job', calendar_ids: [3], updated_at: '2026-11-20T10:00:00Z' }] })
    expect(one('upcoming-all')!.getAttribute('aria-pressed')).toBe('true')
    await click(all('upcoming-calendar').find((chip) => chip.textContent?.includes('Persönlich'))!)
    expect(windows().at(-1)!.get('calendars')).toBe('2')
    expect(one('only-bar')).not.toBeNull()
    await click(all('upcoming-collection')[0])
    expect(one('only-bar')).toBeNull()
    expect(windows().at(-1)!.get('calendars')).toBe('3')
    await click(one('upcoming-all'))
    expect(windows().at(-1)!.get('calendars')).toBeNull()
  })

  it('the plan: columns from the first weekday set, the first calm stretch, runs of free days', () => {
    expect(columnsFrom('2026-11-26', 1).map((column) => [column.kind, column.first, column.last])).toEqual([
      ['this', '2026-11-26', '2026-11-29'],
      ['next', '2026-11-30', '2026-12-06'],
      ['week', '2026-12-07', '2026-12-13'],
      ['later', '2026-12-14', '2027-01-10'],
    ])
    expect(columnsFrom('2026-11-26', 0)[0]).toEqual({ kind: 'this', first: '2026-11-26', last: '2026-11-28' })
    const days = (counts: number[]) => counts.map((count, index) => ({ date: `2026-12-${String(index + 1).padStart(2, '0')}`, count }))
    expect(calmStretch(days([1, 0, 2, 0, 0, 0, 1]))).toEqual({ from: '2026-12-04', to: '2026-12-06' })
    expect(calmStretch(days([1, 0, 2, 1]))).toEqual({ from: '2026-12-02', to: '2026-12-02' })
    expect(calmStretch(days([1, 2]))).toBeNull()
    expect(freeRuns('2026-12-01', '2026-12-07', (date) => (date === '2026-12-03' ? 1 : 0))).toEqual([
      { first: '2026-12-02', last: '2026-12-02', count: 1 },
      { first: '2026-12-04', last: '2026-12-06', count: 3 },
    ])
  })
})
