/** "Demnächst" (asked for while testing, 2026-11-24): the coming events day by day, with the free days between, the
 * chips that set the same choice as "Sammlung" and "Nur diesen", "Weitere 30 Tage", and a click opening an event.
 * Today is Thursday 26 November 2026, 16:05 in Berlin (`viewFakes`). */
import App from './App'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { setViewDay } from './features/views/state'
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
  at(7, 'Training', '2026-11-27 18:30', '2026-11-27 20:00'),
  at(2, 'Urlaub', '2026-12-01', '2026-12-03'),
  at(6, 'Gelbe Tonne', '2026-12-01', '2026-12-01'),
]

async function open(where: string, fake: Partial<Fake> = {}): Promise<void> {
  views({ rows: ROWS(), ...fake })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
  await settle()
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...box.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const windows = () => calls.filter((call) => call.method === 'GET' && call.path.startsWith('/api/events?')).map((call) => new URL(call.path, 'http://x').searchParams)

describe('Demnächst', () => {
  it('stands in the sidebar below "Kalender" and lists the coming days with their events and the free days between', async () => {
    await open('/')
    const places = [...one('sidebar-places')!.querySelectorAll('[data-testid^="nav-"]')].map((link) => link.textContent)
    expect(places).toEqual(['Heute', 'Kalender', 'Demnächst'])
    await click(one('nav-upcoming'))
    expect(one('view-upcoming')).not.toBeNull()
    expect(one('nav-upcoming')!.className).toContain('nt-chosen')
    const days = all('upcoming-day')
    expect(days.map((day) => day.dataset.date)).toEqual(['2026-11-26', '2026-11-27', '2026-12-01'])
    expect(days[0].textContent).toContain('Donnerstag, 26. November')
    expect(days[0].textContent).toContain('heute')
    expect(days[1].textContent).toContain('morgen')
    // Today: the past event quiet, both with time and calendar.
    const today = [...days[0].querySelectorAll<HTMLElement>('[data-testid="upcoming-event"]')]
    expect(today.map((event) => event.textContent)).toEqual(['09:00PlanungArbeit', '18:00ZahnarztPersönlich'])
    expect(today[0].className).toContain('opacity-50')
    // The holiday over three days on its first day, "bis 3. Dezember"; the waste collection quiet beside the day.
    expect(days[2].querySelector('[data-testid="upcoming-event"]')!.textContent).toContain('bis 3. Dezember')
    expect(days[2].querySelector('[data-testid="upcoming-background"]')!.textContent).toBe('Gelbe Tonne')
    // Free days between: 28 to 30 November.
    expect(all('upcoming-gap').map((gap) => gap.textContent)).toContain('3 Tage frei')
    // Thirty days asked for from today.
    const asked = windows().at(-1)!
    expect(Date.parse(asked.get('end')!) - Date.parse(asked.get('start')!)).toBe(30 * 86_400_000)
  })

  it('the chips set the same choice as "Sammlung" and "Nur diesen"', async () => {
    await open('/upcoming', { collections: [{ id: 9, name: 'Job', calendar_ids: [3], updated_at: '2026-11-20T10:00:00Z' }] })
    expect(one('upcoming-all')!.getAttribute('aria-pressed')).toBe('true')
    await click(all('upcoming-calendar').find((chip) => chip.textContent?.includes('Persönlich'))!)
    expect(windows().at(-1)!.get('calendars')).toBe('2')
    expect(one('only-bar')).not.toBeNull()
    expect(all('upcoming-event').map((event) => event.textContent)).toEqual(['18:00ZahnarztPersönlich', 'ganzt.UrlaubPersönlich · bis 3. Dezember'])
    await click(all('upcoming-collection')[0])
    expect(one('only-bar')).toBeNull()
    expect(windows().at(-1)!.get('calendars')).toBe('3')
    expect(one('collection-button')!.textContent).toContain('Job')
    await click(one('upcoming-all'))
    expect(windows().at(-1)!.get('calendars')).toBeNull()
    expect(one('upcoming-all')!.getAttribute('aria-pressed')).toBe('true')
  })

  it('"Weitere 30 Tage" asks for thirty days more; an empty span says so; a click opens the event', async () => {
    await open('/upcoming', { rows: [] })
    expect(one('upcoming-empty')!.textContent).toBe('In den nächsten 30 Tagen steht nichts an.')
    await click(one('upcoming-more'))
    const asked = windows().at(-1)!
    expect(Date.parse(asked.get('end')!) - Date.parse(asked.get('start')!)).toBe(60 * 86_400_000)
    stop()
    start()
    await open('/upcoming')
    await click(all('upcoming-event')[1])
    expect(one('event-panel')).not.toBeNull()
  })
})
