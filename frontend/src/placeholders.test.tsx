/**
 * A refusal whose sentence has a placeholder ("at least {{minimum}} characters", "larger than allowed ({{max_mb}}
 * MB)") must show the number, on every page that shows refusals: the setup, the invitation, the dialog after a session
 * ran out and the settings' cards. Before, the pages kept only the code and showed "{{minimum}}".
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import { api, ApiError } from './api/client'
import de from './i18n/de.json'
import en from './i18n/en.json'
import i18n from './i18n'
import { errorText } from './lib/errors'
import { AuthProvider } from './state/auth'

type Answer = { status: number; body: unknown }
let handler: (method: string, path: string, body: unknown) => Answer | undefined = () => undefined

/** What the server sends along with each code whose sentence has a placeholder. A new such code must be added here. */
const VALUES: Record<string, Record<string, unknown>> = {
  avatar_too_large: { max_mb: 20 },
  password_too_short: { minimum: 12 },
  too_large: { max_mb: 100 },
  fresh_sign_in_required: { minutes: 10, providers: [] },
  invalid_number: { lowest: 1, highest: 3650 },
  attachment_too_large: { name: 'Probe.mov', size: 146800640, max_mb: 25 },
  location_too_long: { max: 2000 },
  too_many_reminders: { max: 10 },
  search_too_many_words: { max: 20 },
  search_word_too_long: { max: 200 },
  import_too_large: { max_mb: 50 },
  import_too_many: { max: 50000 },
  key_name_too_long: { max: 100 },
  keys_too_many: { max: 20 },
}
const WITH_PLACEHOLDER = Object.entries({ ...en.errors, ...de.errors })
  .filter(([, text]) => /\{\{/.test(String(text)))
  .map(([code]) => code)
  .filter((code, index, all) => all.indexOf(code) === index)

const ME = {
  id: 1, name: 'anna', display_name: 'Anna', email: 'anna@example.com', role: 'member', sign_in: 'password', language: '',
  two_factor: false, two_factor_recovery_left: 0, version: '0.1.0', whats_new_seen: '0.1.0',
}

let root: Root
let box: HTMLDivElement

beforeEach(() => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  handler = () => undefined
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      // "Was ist neu" (the shared module wasneu) has nothing unread here.
      const quiet = path.startsWith('/api/whats-new') ? { status: 200, body: { version: '0.1.0', seen: '0.1.0', unread: [], more: false, current: null, earlier: false } } : undefined
      const answer = handler(method, path, body) ?? quiet ?? { status: 200, body: [] }
      return new Response(JSON.stringify(answer.body), { status: answer.status, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})

afterEach(() => {
  act(() => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
})

async function settle(): Promise<void> {
  for (let i = 0; i < 12; i++) await act(async () => await Promise.resolve())
}

async function render(node: React.ReactNode, at = '/'): Promise<void> {
  await act(async () => root.render(<MemoryRouter initialEntries={[at]}>{node}</MemoryRouter>))
  await settle()
}

function type(field: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
  act(() => void field.dispatchEvent(new Event('input', { bubbles: true })))
}

const refusal = (code: string): Answer => ({ status: 422, body: { detail: { code, message: 'No.', ...VALUES[code] } } })

/** The page's text, and that the refusal's sentence is in it with the server's number. */
function shows(code: string): void {
  expect(document.body.textContent).not.toContain('{{')
  // The sentence the page builds (``errorText``): a code of the shared account module in its words (account.*).
  expect(document.body.textContent).toContain(errorText(code, VALUES[code]))
}

const SETUP = { needs_setup: true, code_required: true, signed_in: false, version: '0.1.0', min_password: 12, data_on_share: false }

describe.each(['en', 'de'])('no raw placeholder in a refusal (%s)', (language) => {
  beforeEach(async () => {
    await i18n.changeLanguage(language)
  })

  it('every code with a placeholder has the values the server sends', () => {
    expect(WITH_PLACEHOLDER.sort()).toEqual(Object.keys(VALUES).sort())
  })

  it('TIME-2 AC-10 the setup page says the minimum when it checks the password itself', async () => {
    handler = (method, path) => (path === '/api/setup' && method === 'GET' ? { status: 200, body: SETUP } : undefined)
    const { SetupPage } = await import('./pages/AuthPages')
    await render(<AuthProvider><SetupPage /></AuthProvider>, '/setup')
    const fields = [...box.querySelectorAll('input')]
    type(fields[0], 'AB12-CD34-EF56')
    type(fields[1], 'Robin')
    type(fields[4], 'eleven char')
    await act(async () => box.querySelector('form')!.requestSubmit())
    await settle()
    shows('password_too_short')
  })

  it.each(Object.keys(VALUES))('the setup page shows the server\'s %s with its number', async (code) => {
    handler = (method, path) => {
      if (path === '/api/setup' && method === 'GET') return { status: 200, body: SETUP }
      if (path === '/api/setup' && method === 'POST') return refusal(code)
      return undefined
    }
    const { SetupPage } = await import('./pages/AuthPages')
    await render(<AuthProvider><SetupPage /></AuthProvider>, '/setup')
    const fields = [...box.querySelectorAll('input')]
    type(fields[1], 'Robin')
    type(fields[4], 'twelve chars')
    type(fields[5], 'twelve chars')
    await act(async () => box.querySelector('form')!.requestSubmit())
    await settle()
    shows(code)
  })

  it.each(Object.keys(VALUES))('the invitation shows the server\'s %s with its number', async (code) => {
    handler = (method, path) => {
      if (path === '/api/setup') return { status: 200, body: { ...SETUP, needs_setup: false } }
      if (path === '/api/auth/methods') return { status: 200, body: { password: true, providers: [] } }
      if (path.startsWith('/api/invite/') && method === 'GET') return { status: 200, body: { by: 'Robin', email: '', role: 'member', min_password: 12, password: true, signed_in_as: null } }
      if (path.startsWith('/api/invite/') && method === 'POST') return refusal(code)
      return undefined
    }
    const { InvitePage } = await import('./pages/AuthPages')
    await render(
      <AuthProvider>
        <Routes>
          <Route path="/invite/:token" element={<InvitePage />} />
        </Routes>
      </AuthProvider>,
      '/invite/abcdefghijklmnopqrstuvwxyz',
    )
    const fields = [...box.querySelectorAll('input')]
    type(fields[0], 'Lena')
    type(fields[3], 'eleven char')
    type(fields[4], 'eleven char')
    await act(async () => box.querySelector('form')!.requestSubmit())
    await settle()
    shows(code)
  })

  it.each(Object.keys(VALUES))('the dialog after a session ran out shows %s with its number', async (code) => {
    let expired = true
    handler = (method, path) => {
      if (path === '/api/setup') return { status: 200, body: { ...SETUP, needs_setup: false, signed_in: true } }
      if (path === '/api/auth/me') return { status: 200, body: ME }
      if (path === '/api/auth/methods') return { status: 200, body: { password: true, providers: [] } }
      if (path === '/api/probe' && expired) return { status: 401, body: { detail: { code: 'sign_in_required' } } }
      if (path === '/api/auth/login' && method === 'POST') return refusal(code)
      return undefined
    }
    const { AppShell } = await import('./components/AppShell')
    await render(
      <AuthProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<p>note</p>} />
          </Route>
        </Routes>
      </AuthProvider>,
    )
    await act(async () => void (await api('/api/probe', { method: 'PUT', body: {} }).catch(() => undefined)))
    await settle()
    expired = false
    const password = document.querySelector<HTMLInputElement>('[data-testid="session-expired"] input[type="password"]')!
    type(password, 'twelve chars')
    await act(async () => document.querySelector<HTMLFormElement>('[data-testid="session-expired"]')!.requestSubmit())
    await settle()
    shows(code)
  })

  it.each(Object.keys(VALUES))('the settings\' cards show %s with its number', async (code) => {
    // The cards' line of what came of a change, in the kit's box (`Outcome`, plan "Einstellungen").
    const { Outcome } = await import('./pages/settings/Outcome')
    await render(<Outcome problem={new ApiError(422, code, VALUES[code])} />)
    shows(code)
  })
})
