/**
 * TIME-29 Anbindung der iOS-App on the page, since the shared module geraete 0.1.0: the card "Geräte" stands first in
 * Settings › My account › Connections (its own behaviour is tested in `nexgeraete.test.tsx`), "Auf iPhone und Co"
 * keeps the calendar account alone, and the page `/app-connect` the app opens to sign in (AC-5) signs in first, asks,
 * and hands the code to the app through the module's routes under `/api/geraete`. The server's side is in
 * `backend/tests/test_time29.py` and the module's contract `backend/tests/nexgeraete/`.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

import { AppConnectPage } from './features/app/AppConnectPage'
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
let root: Root
let box: HTMLDivElement

function reply(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function answer(call: Call): Response {
  const { method, path } = call
  if (method === 'GET' && path === '/api/geraete/app-devices') return reply(200, { devices: [], max: 30, app_store_url: null, has_app: true })
  if (method === 'POST' && path === '/api/geraete/app-connect') {
    if (call.body?.state === 'refused-state-1') return reply(403, { detail: { code: 'app_disabled', message: 'x' } })
    return reply(200, { redirect: `nextime://connected?code=c0de&state=${String(call.body?.state)}` })
  }
  return reply(404, { detail: { code: 'not_found', message: 'Not found.' } })
}

beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  calls = []
  scene.assigned.length = 0
  scene.status = 'signedIn'
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

async function click(element: HTMLElement | null): Promise<void> {
  expect(element).not.toBeNull()
  await act(async () => element!.click())
  await settle()
}

describe('TIME-29 the card stands first under Connections (plan "Einstellungen", E4)', () => {
  it('TIME-29 AC-7 Connections begins with "Geräte"', async () => {
    const { ConnectionsArea } = await import('./features/keys/ConnectionsArea')
    await render(<ConnectionsArea />)
    expect(document.body.querySelector('[data-card]')!.getAttribute('data-card')).toBe('devices')
    expect(calls).toContainEqual({ method: 'GET', path: '/api/geraete/app-devices', body: undefined })
  })

  it('TIME-29 AC-7 "Auf iPhone und Co" keeps the calendar account alone', async () => {
    const { AreaContent } = await import('./pages/settings/SettingsAreas')
    await render(<AreaContent area="devices" />)
    expect(document.body.querySelector('[data-card="devices"]')).toBeNull()
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
    expect(document.body.textContent).toContain('nextime-App mit deinem Konto verbinden?')
    expect($('app-connect-name')?.textContent).toContain('Mika')
    expect($('app-connect-login')?.textContent).toContain('mika')
    expect(calls.some((call) => call.path.endsWith('/app-connect'))).toBe(false)
    await click($('app-connect-confirm'))
    expect(calls).toContainEqual({ method: 'POST', path: '/api/geraete/app-connect', body: { state: 'AbCdEfGhIjKlMnOpQrStUv' } })
    expect(scene.assigned).toEqual(['nextime://connected?code=c0de&state=AbCdEfGhIjKlMnOpQrStUv'])
    expect($('app-connect')?.getAttribute('data-state')).toBe('done')
  })

  it('TIME-29 AC-5 cancel tells the app and connects nothing', async () => {
    await render(page, '/app-connect?state=AbCdEfGhIjKlMnOpQrStUv')
    await click($('app-connect-cancel'))
    expect(scene.assigned).toEqual(['nextime://connected?error=cancelled&state=AbCdEfGhIjKlMnOpQrStUv'])
    expect(calls.some((call) => call.path.endsWith('/app-connect'))).toBe(false)
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
