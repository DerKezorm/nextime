/** Events in the month with a dot or tinted in their calendar's color (Einstellungen › Darstellung, asked for while
 * testing, 2026-11-24): the choice in the settings, and the month drawing either way. */
import App from './App'
import { setViewDay } from './features/views/state'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, choose, me, ok, render, settle, start, stop } from './testing'
import { at, BERLIN, NOW, robin, views } from './viewFakes'

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

async function open(where: string, style?: 'dot' | 'tint'): Promise<void> {
  views({
    rows: [at(3, 'Planung', '2026-11-26 09:00', '2026-11-26 10:00'), at(4, '', '2026-11-26 11:00', '2026-11-26 12:00', { busy_only: true })],
    more: (method, path) => {
      if (path === '/api/auth/me') return { status: 200, body: me({ id: 1, display: { ...BERLIN, ...(style ? { month_style: style } : {}) } }) }
      if (method === 'PUT' && path === '/api/me/display') return ok(robin)
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

const events = () => [...box.querySelectorAll<HTMLElement>('[data-testid="month-event"]')]

describe('events in the month: dot or tinted', () => {
  it('the choice stands under Darstellung, with a dot from the start, and is saved with the account', async () => {
    await open('/settings/appearance')
    const select = box.querySelector<HTMLSelectElement>('#display-month-style')!
    expect([...select.options].map((option) => option.textContent)).toEqual(['mit Punkt', 'farbig hinterlegt'])
    expect(select.value).toBe('dot')
    await choose(select, 'tint')
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/me/display')!.body).toEqual({ month_style: 'tint' })
  })

  it('with a dot: the dot in the calendar\'s color, no ground', async () => {
    await open('/month')
    const planung = events().find((event) => event.textContent?.includes('Planung'))!
    expect(planung.dataset.style).toBe('dot')
    expect(planung.querySelector('[data-color="teal"]')).not.toBeNull()
    expect(planung.className).not.toContain('nt-tint')
  })

  it('tinted: no dot, the whole row on the calendar\'s color; "Belegt" stays gray', async () => {
    await open('/month', 'tint')
    const planung = events().find((event) => event.textContent?.includes('Planung'))!
    expect(planung.dataset.style).toBe('tint')
    expect(planung.className).toContain('nt-tint')
    expect(planung.querySelector('[data-color]')).toBeNull()
    expect(planung.style.getPropertyValue('--k')).toContain('teal')
    const busy = events().find((event) => event.className.includes('text-mist-500'))!
    expect(busy.style.getPropertyValue('--k')).toContain('gray')
    const css = readFileSync('src/styles/index.css', 'utf-8')
    expect(css).toMatch(/\.nt-tint \{\n {2}background: color-mix\(in srgb, var\(--k\) 16%, transparent\);/)
    expect(css).toContain('.nt-month-event.nt-tint {')
  })
})
