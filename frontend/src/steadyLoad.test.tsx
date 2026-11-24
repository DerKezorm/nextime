/** Opening a page loads steadily (found while testing, 2026-11-24): a calendar shown alone is never shown as
 * "everything" first, and the page asks for the list of calendars once, not once for every part of it. */
import App from './App'
import i18n from './i18n'
import { forgetPrefs, prefKey } from './lib/devicePref'
import { setViewDay } from './features/views/state'
import { AuthProvider } from './state/auth'
import { calls, render, settle, start, stop } from './testing'
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
})

async function open(where: string): Promise<void> {
  views({ rows: theWeek() })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
  await settle()
}

const windows = () => calls.filter((call) => call.method === 'GET' && /^\/api\/(events\?|events\/year\?|search\/upcoming)/.test(call.path))

describe('a page loads steadily', () => {
  it('TIME-13 AC-11 a calendar shown alone is asked for alone from the start, never "everything" first', async () => {
    localStorage.setItem(prefKey('only', 1), '3')
    await open('/month')
    expect(windows().length).toBeGreaterThan(0)
    for (const call of windows()) expect(new URL(call.path, 'http://x').searchParams.get('calendars')).toBe('3')
    stop()
    start()
    localStorage.setItem(prefKey('only', 1), '3')
    forgetPrefs()
    await open('/year')
    expect(windows().length).toBeGreaterThan(0)
    for (const call of windows()) expect(new URL(call.path, 'http://x').searchParams.get('calendars')).toBe('3')
  })

  it('TIME-12 EC-9 the list of calendars is asked once for the whole page', async () => {
    await open('/month')
    expect(calls.filter((call) => call.method === 'GET' && call.path === '/api/calendars')).toHaveLength(1)
  })
})
