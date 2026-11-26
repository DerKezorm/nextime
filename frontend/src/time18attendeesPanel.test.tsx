/**
 * TIME-18 AC-9 (plan "Apps verbinden" V30): the field "Teilnehmer" is only there for calendars of iCloud and Google,
 * which send the invitations themselves. A calendar of nextime has no field, and an event of a calendar shared as
 * "busy only" shows no field either.
 */
import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { CalendarList, EventDetail } from './features/calendars/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, choose, click, me, ok, render, serve, signedIn, start, stop } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })
const NOW = new Date('2026-11-26T15:05:00Z')

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function server(lists: CalendarList, detail: EventDetail | null = null) {
  serve(
    signedIn(anna, (method, path) => {
      if (path.startsWith('/api/calendars')) return ok(lists)
      if (path.startsWith('/api/people')) return ok({ connected: false, people: [] })
      if (path.startsWith('/api/events?') && detail)
        return ok([{ id: detail.id, occurrence: detail.occurrence, calendar_id: detail.calendar_id, title: detail.title, busy: detail.busy, busy_only: detail.busy_only, recurring: detail.recurring, all_day: false, start: '2026-11-26T17:30:00Z', end: '2026-11-26T19:00:00Z', time_zone: 'Europe/Berlin' }])
      if (path.startsWith(`/api/events/${detail?.id}`) && method === 'GET') return ok(detail)
      return undefined
    }),
  )
}

async function open(): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    '/',
  )
}

const attendees = () => document.querySelector('[data-testid="field-attendees"]')
const panel = () => document.querySelector('[data-testid="event-panel"]')

it('TIME-18 AC-9 shows the field for iCloud and Google, not for a calendar of nextime', async () => {
  server(calendarList([calendar(1, 'Persönlich', { default: true }), calendar(2, 'Arbeit', { source: 'icloud' }), calendar(3, 'Elternbeirat', { source: 'google' })]))
  await open()
  await click(box.querySelector('[data-testid="new-event"]'))
  expect(panel()).not.toBeNull()
  expect(attendees()).toBeNull()
  const chooser = document.querySelector<HTMLSelectElement>('[data-testid="field-calendar"]')!
  await choose(chooser, '2')
  expect(attendees()).not.toBeNull()
  await choose(chooser, '3')
  expect(attendees()).not.toBeNull()
  await choose(chooser, '1')
  expect(attendees()).toBeNull()
})

it('TIME-18 AC-9 shows no field for an event of a calendar shared as busy only', async () => {
  const lists = calendarList([calendar(1, 'Persönlich', { default: true }), calendar(4, 'Annas', { group: 'shared', source: 'icloud', right: 'busy', writable: false, owner: 'Anna' })])
  server(lists, eventDetail({ calendar_id: 4, writable: false, right: 'busy', busy_only: true, title: '' }))
  await open()
  await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
  expect(document.querySelector('[data-testid="read-only"]')).not.toBeNull()
  expect(attendees()).toBeNull()
})
