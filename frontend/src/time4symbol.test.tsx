/** TIME-4, "Kalender-Symbol wie nexnotes": a calendar's symbol is chosen in "New calendar" and when editing, beside the
 * color, with nexnotes' picker and its search; it shows in the calendar's color in the list instead of the square, and
 * without one the square stays. Calendars of a source pick one the same way. */
import App from './App'
import { calendar, calendarList } from './calendarFakes'
import type { CalendarList } from './features/calendars/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, render, serve, signedIn, start, stop, type } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: 'auto', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.restoreAllMocks()
})

function server(list: () => CalendarList) {
  serve(signedIn(anna, (method, path) => (path.startsWith('/api/calendars') ? { status: method === 'POST' ? 201 : 200, body: list() } : undefined)))
}

async function open(): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    '/',
  )
}

const list = () => box.querySelector('[data-testid="calendar-list"]')!

describe('TIME-4 symbol', () => {
  it('TIME-4 symbol shows in the calendar color in the list, without one the square stays', async () => {
    server(() => calendarList([calendar(1, 'Persönlich'), calendar(2, 'Reisen', { color: 'blue', icon: 'plane' }), calendar(3, 'Alt', { icon: 'no-such-symbol' })]))
    await open()
    const rows = [...list().querySelectorAll('[data-testid="calendar-toggle"]')]
    const mark = rows[1].querySelector('svg[data-symbol]')!
    expect(mark.getAttribute('data-symbol')).toBe('plane')
    expect((mark as SVGElement).style.color).toBe('var(--color-cal-blue)')
    expect(rows[0].querySelector('svg[data-symbol]')).toBeNull()
    expect(rows[0].querySelector('span[data-color]')).not.toBeNull()
    // A name the list does not know draws the square, never nothing.
    expect(rows[2].querySelector('svg[data-symbol]')).toBeNull()
    expect(rows[2].querySelector('span[data-color]')).not.toBeNull()
  })

  it('TIME-4 symbol is chosen in "New calendar" beside the color, found by its name, and sent along', async () => {
    let current = calendarList([calendar(1, 'Persönlich')])
    server(() => current)
    await open()
    await click(box.querySelector('[data-testid="add-calendar"]'))
    await click(document.querySelector('[data-testid="way-own"]'))
    const dialog = document.querySelector<HTMLElement>('[data-testid="calendar-dialog"]')!
    const picker = dialog.querySelector('[data-testid="calendar-symbol"]')!
    expect(picker.textContent).toContain('Symbol')
    // Nothing chosen yet: the preview is the color square.
    expect(dialog.querySelector('[data-testid="calendar-look"] span[data-color="slate"]')).not.toBeNull()
    type(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!, 'Urlaub')
    type(picker.querySelector<HTMLInputElement>('[data-testid="symbol-search"]')!, 'Strand')
    const found = [...picker.querySelectorAll('[role="radio"]')]
    expect(found.map((node) => node.getAttribute('data-symbol'))).toContain('beach')
    await click(picker.querySelector('[data-symbol="beach"]'))
    expect(picker.querySelector('[data-symbol="beach"]')?.getAttribute('aria-checked')).toBe('true')
    expect(dialog.querySelector('[data-testid="calendar-look"] svg')?.getAttribute('data-symbol')).toBe('beach')
    current = calendarList([calendar(1, 'Persönlich'), calendar(2, 'Urlaub', { icon: 'beach' })])
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/calendars')?.body).toMatchObject({ name: 'Urlaub', icon: 'beach' })
  })

  it('TIME-4 symbol editing a calendar of a source shows its symbol and "Ohne Symbol" takes it away', async () => {
    const arbeit = calendar(2, 'Arbeit', { color: 'green', icon: 'briefcase', group: 'icloud', origin: { id: 7, kind: 'icloud', label: 'iCloud', state: 'ok', synced_at: null } })
    server(() => calendarList([calendar(1, 'Persönlich'), arbeit]))
    await open()
    await click(list().querySelectorAll('[aria-haspopup="menu"]')[1])
    await click(byText('Bearbeiten …'))
    const dialog = document.querySelector<HTMLElement>('[data-testid="calendar-dialog"]')!
    expect(dialog.querySelector('[data-symbol="briefcase"][role="radio"]')?.getAttribute('aria-checked')).toBe('true')
    await click(dialog.querySelector('[data-testid="calendar-symbol-none"]'))
    expect(dialog.querySelector('[data-testid="calendar-look"] svg')).toBeNull()
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(calls.find((call) => call.method === 'PATCH' && call.path === '/api/calendars/2')?.body).toMatchObject({ icon: '' })
  })
})
