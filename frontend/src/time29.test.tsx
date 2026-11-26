/**
 * TIME-29 Anbindung der iOS-App on the page, as nexnotes: the card "Geräte" in Settings › My account › On iPhone and co
 * with name, kind, last contact, notifications and "Trennen" (AC-4, AC-7); "iPhone oder iPad verbinden" with the QR
 * code, three steps, ten minutes and "neu erzeugen", closing itself once the app used the code (AC-1); the page
 * `/app-connect` the app opens to sign in (AC-5). The server's side is in `backend/tests/test_time29.py`.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { qrSource, type AppDevice, type Pairing } from './features/app/api'
import { AppConnectPage } from './features/app/AppConnectPage'
import { AppDevicesSection } from './features/app/AppDevicesSection'
import { POLL_MS } from './features/app/ConnectDialog'
import i18n from './i18n'

const scene = vi.hoisted(() => ({
  me: { id: 1, name: 'mika', display_name: 'Mika' } as Record<string, unknown> | null,
  status: 'signedIn' as string,
  assigned: [] as string[],
}))

vi.mock('./lib/providers', async (original) => ({
  ...(await original<typeof import('./lib/providers')>()),
  go: (url: string) => void scene.assigned.push(url),
}))

vi.mock('./state/auth', async (original) => ({
  ...(await original<typeof import('./state/auth')>()),
  useAuth: () => ({ me: scene.me, status: scene.status, setMe: () => undefined, refresh: async () => undefined, signOut: async () => undefined }),
}))

type Call = { method: string; path: string; body: Record<string, unknown> | undefined }
let calls: Call[] = []
let devices: AppDevice[] = []
let pairings = 0
let pairingState: 'waiting' | 'used' | 'expired' = 'waiting'
let root: Root
let box: HTMLDivElement

const HOUR = 3_600_000

function pairing(): Pairing {
  pairings += 1
  return {
    id: `p${pairings}`,
    secret: `s${pairings}`,
    expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
    link: `nextime://connect?server=https%3A%2F%2Fkalender.example.com&pairing=p${pairings}&secret=s${pairings}`,
    address: 'https://kalender.example.com',
    qr_svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>p${pairings}</title></svg>`,
  }
}

function reply(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function answer(call: Call): Response {
  const { method, path } = call
  if (method === 'GET' && path === '/api/app-devices') return reply(200, devices)
  if (method === 'DELETE' && path.startsWith('/api/app-devices/')) {
    devices = devices.filter((device) => `/api/app-devices/${device.id}` !== path)
    return reply(204, null)
  }
  if (method === 'POST' && path === '/api/app-pairing') return reply(201, pairing())
  if (method === 'GET' && path.startsWith('/api/app-pairing/')) return reply(200, { state: pairingState, device: null })
  if (method === 'DELETE' && path.startsWith('/api/app-pairing/')) return reply(204, null)
  if (method === 'POST' && path === '/api/app-connect') {
    if (call.body?.state === 'refused-state-1') return reply(403, { detail: { code: 'second_factor_setup_required', message: 'x' } })
    return reply(200, { link: `nextime://connected?code=c0de&state=${String(call.body?.state)}` })
  }
  return reply(404, { detail: { code: 'not_found', message: 'Not found.' } })
}

beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  calls = []
  pairings = 0
  pairingState = 'waiting'
  scene.assigned.length = 0
  scene.status = 'signedIn'
  devices = [
    { id: 1, name: 'iPhone von Mika', kind: 'ios', created_at: new Date(Date.now() - 48 * HOUR).toISOString(), last_seen_at: new Date(Date.now() - 2 * 60_000).toISOString(), push: true },
    { id: 2, name: 'Mac mini', kind: 'mac', created_at: new Date(Date.now() - 48 * HOUR).toISOString(), last_seen_at: null, push: false },
  ]
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), window.location.origin)
      const call = { method: init?.method ?? 'GET', path: url.pathname, body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined }
      calls.push(call)
      return answer(call)
    }),
  )
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
  await i18n.changeLanguage('de')
})

afterEach(() => {
  act(() => root.unmount())
  box.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function settle(rounds = 12): Promise<void> {
  for (let i = 0; i < rounds; i++) await act(async () => await Promise.resolve())
}

async function render(node: React.ReactNode, at = '/'): Promise<void> {
  await act(async () => root.render(<MemoryRouter initialEntries={[at]}>{node}</MemoryRouter>))
  await settle()
}

const $ = (testId: string) => document.body.querySelector<HTMLElement>(`[data-testid="${testId}"]`)
const $$ = (testId: string) => [...document.body.querySelectorAll<HTMLElement>(`[data-testid="${testId}"]`)]

async function click(element: HTMLElement | null): Promise<void> {
  expect(element).not.toBeNull()
  await act(async () => element!.click())
  await settle()
}

describe('TIME-29 devices in Settings, On iPhone and co', () => {
  it('TIME-29 AC-7 EC-8 lists every device with its kind, last contact and notifications on or off', async () => {
    await render(<AppDevicesSection />)
    expect($$('app-device-name').map((element) => element.textContent)).toEqual(['iPhone von Mika', 'Mac mini'])
    const [phone, mac] = $$('app-device-meta').map((element) => element.textContent)
    expect(phone).toBe('iOS-App · zuletzt vor 2 Minuten')
    expect(mac).toBe('iPad-App auf dem Mac · noch nicht gemeldet')
    // Notifications on or off as the kit's badge beside the name (plan "Einstellungen", Verbindungen 1).
    const badges = $$('app-device-row').map((row) => row.querySelector('[data-tone]'))
    expect(badges.map((badge) => [badge?.textContent, badge?.getAttribute('data-tone')])).toEqual([['Mitteilungen an', 'on'], ['Mitteilungen aus', 'off']])
    const disconnect = $$('app-device-disconnect')[0]
    expect([disconnect.dataset.kind, disconnect.dataset.size]).toEqual(['danger', 'sm'])
  })

  it('TIME-29 AC-7 disconnects a device after asking', async () => {
    await render(<AppDevicesSection />)
    await click($$('app-device-disconnect')[0])
    expect(document.body.textContent).toContain('„iPhone von Mika“ trennen?')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!
    await click([...dialog.querySelectorAll<HTMLElement>('button')].find((button) => button.textContent === 'Trennen') ?? null)
    expect(calls).toContainEqual({ method: 'DELETE', path: '/api/app-devices/1', body: undefined })
    expect($$('app-device-name').map((element) => element.textContent)).toEqual(['Mac mini'])
  })

  it('TIME-29 AC-7 says when no device is connected', async () => {
    devices = []
    await render(<AppDevicesSection />)
    expect($('app-devices-none')?.textContent).toBe('Noch kein Gerät verbunden.')
  })
})

describe('TIME-29 connecting an iPhone or iPad', () => {
  it('TIME-29 AC-1 shows the QR code with three steps, valid 10 minutes, and the address without a camera', async () => {
    await render(<AppDevicesSection />)
    // As nexnotes (gallery, E39): primary, md, the symbol Smartphone.
    expect([$('app-devices-connect')!.dataset.kind, $('app-devices-connect')!.dataset.size, $('app-devices-connect')!.dataset.icon]).toEqual(['primary', 'md', 'Smartphone'])
    await click($('app-devices-connect'))
    const dialog = $('app-connect-dialog')!
    expect(document.body.textContent).toContain('iPhone oder iPad verbinden')
    expect(($('app-qr') as HTMLImageElement).src).toBe(qrSource(pairing().qr_svg.replace('p2', 'p1')))
    expect([...dialog.querySelectorAll('li')].map((item) => item.textContent)).toEqual([
      'nextime auf dem iPhone oder iPad öffnen.',
      '„Server hinzufügen“ tippen und den Code scannen.',
      'Fertig. Deine Kalender laden, und die Anmeldung wandert über deine Apple-ID auf deine anderen Geräte.',
    ])
    expect(dialog.textContent).toContain('Gilt 10 Minuten')
    expect(document.body.querySelector<HTMLElement>('[data-testid="app-renew"]')!.textContent).toBe('neu erzeugen')
    expect($('app-address')?.textContent).toBe('Ohne Kamera: In der App die Adresse kalender.example.com eingeben und anmelden.')
  })

  it('TIME-29 AC-1 makes a new code and withdraws the one before', async () => {
    await render(<AppDevicesSection />)
    await click($('app-devices-connect'))
    await click($('app-renew'))
    expect(calls.filter((call) => call.method === 'POST' && call.path === '/api/app-pairing')).toHaveLength(2)
    expect(calls).toContainEqual({ method: 'DELETE', path: '/api/app-pairing/p1', body: undefined })
    expect(decodeURIComponent(($('app-qr') as HTMLImageElement).src)).toContain('<title>p2</title>')
  })

  it('TIME-29 AC-1 closes itself once the app used the code, and the list shows the new device', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
    await render(<AppDevicesSection />)
    await click($('app-devices-connect'))
    pairingState = 'used'
    devices = [...devices, { id: 3, name: 'iPad', kind: 'ipados', created_at: new Date().toISOString(), last_seen_at: new Date().toISOString(), push: false }]
    await act(async () => vi.advanceTimersByTime(POLL_MS))
    await settle()
    expect($('app-connect-dialog')).toBeNull()
    expect($$('app-device-name').map((element) => element.textContent)).toContain('iPad')
    // A code that was used is not withdrawn.
    expect(calls.some((call) => call.method === 'DELETE' && call.path.startsWith('/api/app-pairing/'))).toBe(false)
  })

  it('TIME-29 AC-1 says when the code ran out after ten minutes', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date'] })
    await render(<AppDevicesSection />)
    await click($('app-devices-connect'))
    await act(async () => vi.advanceTimersByTime(10 * 60_000 + POLL_MS))
    await settle()
    expect($('app-expired')?.textContent).toBe('Der Code ist abgelaufen.')
    expect($('app-qr')).toBeNull()
  })

  it('TIME-29 AC-1 withdraws an unused code when closed', async () => {
    await render(<AppDevicesSection />)
    await click($('app-devices-connect'))
    await click($('app-close'))
    expect(calls).toContainEqual({ method: 'DELETE', path: '/api/app-pairing/p1', body: undefined })
    expect($('app-connect-dialog')).toBeNull()
  })
})

describe('TIME-29 the card stands first under Connections (plan "Einstellungen", E4)', () => {
  it('TIME-29 AC-7 Connections begins with "Geräte"', async () => {
    const { ConnectionsArea } = await import('./features/keys/ConnectionsArea')
    await render(<ConnectionsArea />)
    expect(document.body.querySelector('[data-card]')!.getAttribute('data-card')).toBe('devices')
    expect($('app-devices-connect')?.textContent).toBe('iPhone oder iPad verbinden')
  })

  it('TIME-29 AC-7 "Auf iPhone und Co" keeps the calendar account alone', async () => {
    const { AreaContent } = await import('./pages/settings/SettingsAreas')
    await render(<AreaContent area="devices" />)
    expect($('app-devices-connect')).toBeNull()
  })
})

describe('TIME-29 the page /app-connect', () => {
  const page = (
    <Routes>
      <Route path="/app-connect" element={<AppConnectPage />} />
      <Route path="/login" element={<p data-testid="login">login</p>} />
    </Routes>
  )

  it('TIME-29 AC-5 asks before connecting and hands the code to the app', async () => {
    await render(page, '/app-connect?state=AbCdEfGhIjKlMnOpQrStUv')
    expect(document.body.textContent).toContain('Die nextime-App mit diesem Konto verbinden?')
    expect(document.body.textContent).toContain('Angemeldet als Mika.')
    expect(calls.some((call) => call.path === '/api/app-connect')).toBe(false)
    await click($('app-connect-confirm'))
    expect(calls).toContainEqual({ method: 'POST', path: '/api/app-connect', body: { state: 'AbCdEfGhIjKlMnOpQrStUv' } })
    expect(scene.assigned).toEqual(['nextime://connected?code=c0de&state=AbCdEfGhIjKlMnOpQrStUv'])
    expect($('app-connect-done')).not.toBeNull()
  })

  it('TIME-29 AC-5 cancel tells the app and connects nothing', async () => {
    await render(page, '/app-connect?state=AbCdEfGhIjKlMnOpQrStUv')
    await click($('app-connect-cancel'))
    expect(scene.assigned).toEqual(['nextime://connected?error=cancelled&state=AbCdEfGhIjKlMnOpQrStUv'])
    expect(calls.some((call) => call.path === '/api/app-connect')).toBe(false)
  })

  it('TIME-29 AC-5 signs in first and comes back to the page', async () => {
    scene.status = 'signedOut'
    await render(
      <Routes>
        <Route path="/app-connect" element={<AppConnectPage />} />
        <Route path="/login" element={<LoginProbe />} />
      </Routes>,
      '/app-connect?state=AbCdEfGhIjKlMnOpQrStUv',
    )
    expect($('login-next')?.textContent).toBe('/app-connect?state=AbCdEfGhIjKlMnOpQrStUv')
  })

  it('TIME-29 AC-5 says what went wrong', async () => {
    await render(page, '/app-connect?state=refused-state-1')
    await click($('app-connect-confirm'))
    expect($('app-connect-problem')).not.toBeNull()
    expect(scene.assigned).toEqual([])
  })

  it('TIME-29 AC-5 without a state from the app does nothing', async () => {
    await render(page, '/app-connect')
    expect($('app-connect-invalid')).not.toBeNull()
    expect($('app-connect-confirm')).toBeNull()
  })
})

function LoginProbe() {
  const location = useLocation()
  return <p data-testid="login-next">{new URLSearchParams(location.search).get('next')}</p>
}
