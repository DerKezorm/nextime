/**
 * The shared module mcp in nextime's pages (bauplaene/mcp 1.1.0, TIME-26): "Rechte je Werkzeug" at every MCP key and
 * program in the card "KI-Werkzeuge (MCP)", with nextime's words for its tools; "Wartende Rückfragen" leading to the
 * page of one request (`/mcp/requests/<id>`); the consent page `/oauth/authorize` only after the sign-in, which comes
 * back with the program's query; the operator's settings under "MCP-Zugang". The module's own parts are tested in
 * `nexmcp.test.tsx`; the server's side in `backend/tests/nexmcp` and `test_mcp_module.py`.
 */
import { AuthProvider } from './state/auth'
import App from './App'
import i18n from './i18n'
import { box, calls, click, KEYS_LISTING, me, ok, render, serve, signedIn, start, stop } from './testing'

const KEY = {
  id: 'mcp-3', kind: 'mcp', name: 'Planer', right: 'write', prefix: 'nt_mcp_AbCd', created_at: '2026-11-26T08:00:00Z',
  last_used_at: null, expires_at: null, expired: false, expiring: false, blocked: false, areas: null, options: {},
  program: false,
}
const RIGHTS = {
  key: { id: 'mcp-3', name: 'Planer', program: false },
  level: 'write',
  tools: [
    { name: 'list_events', group: 'read', right: 'allow', asks_never: true, reason: null, always: false },
    { name: 'delete_event', group: 'risky', right: 'allow', asks_never: false, reason: null, always: false },
    { name: 'request_status', group: 'read', right: 'allow', asks_never: true, reason: null, always: true },
  ],
}
const WAITING = {
  id: 7, key_id: 'mcp-3', key_name: 'Planer', tool: 'delete_event', arguments: { event_id: 12 }, status: 'waiting',
  created_at: '2026-11-26T08:00:00Z', expires_at: '2026-11-27T08:00:00Z', decided_at: null, decided_by: '',
  summary: 'Put the event 12 into the trash', dangerous: true, result: null,
}
const CONSENT = {
  app: 'nextime', client: { id: 'client-1', name: 'Probe client' }, redirect_uri: 'http://127.0.0.1:33418/callback',
  redirect_host: '127.0.0.1:33418', levels: ['read', 'write'], level: 'read', areas: [{ id: 1, name: 'Persönlich' }],
  extras: [], ai_allowed: true,
}

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(stop)

function server(account = me()) {
  return signedIn(account, (_method, path) => {
    if (path === '/api/connections/keys') {
      const kinds = [...KEYS_LISTING.kinds, { ...KEYS_LISTING.kinds[0], id: 'mcp', prefix: 'nt_mcp_', options: [] }]
      return ok({ ...KEYS_LISTING, programs: true, kinds, keys: [KEY] })
    }
    if (path === '/api/connections/mcp/keys/mcp-3/rights') return ok(RIGHTS)
    if (path.startsWith('/api/connections/mcp/requests/7')) return ok(WAITING)
    if (path.startsWith('/api/connections/mcp/requests')) return ok([WAITING])
    if (path.startsWith('/api/oauth/authorize')) return ok(CONSENT)
    return undefined
  })
}

describe('the shared module mcp in nextime', () => {
  it('each MCP key has "Rechte je Werkzeug" with nextime\'s words for its tools', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/connections')
    const card = box.querySelector('[data-card="tools"]') ?? box.querySelector('[data-testid="tools-card"]')!
    const button = card.querySelector<HTMLElement>('[data-testid="tool-rights"]')
    expect(button?.textContent).toContain('Rechte je Werkzeug')
    await click(button)
    const dialog = document.querySelector('[data-testid="tool-rights-dialog"]')!
    expect(dialog.textContent).toContain('Termine auflisten')
    expect(dialog.textContent).toContain('Termin in den Papierkorb legen')
    expect(calls.some((call) => call.path === '/api/connections/mcp/keys/mcp-3/rights')).toBe(true)
  })

  it('a request that waits for a yes is listed; a dangerous one leads with "Ansehen" to its page', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/connections')
    const waiting = box.querySelector('[data-testid="pending-requests"]')!
    expect(waiting.textContent).toContain('Planer')
    const view = [...waiting.querySelectorAll<HTMLAnchorElement>('a')].find((link) => link.getAttribute('href') === '/mcp/requests/7')
    expect(view).toBeDefined()
    await render(<AuthProvider><App /></AuthProvider>, '/mcp/requests/7')
    expect(box.querySelector('[data-testid="request-page"]')?.textContent).toContain('Termin in den Papierkorb legen')
  })

  it('the consent page waits for a sign-in and comes back with the program\'s query', async () => {
    serve((_method, path) => (path === '/api/auth/me' ? { status: 401, body: { detail: { code: 'sign_in_required' } } }
      : path === '/api/setup' ? ok({ needs_setup: false, code_required: true, signed_in: false, version: '0.1.0', min_password: 12 })
        : path === '/api/auth/methods' ? ok({ password: true, providers: [] }) : undefined))
    await render(<AuthProvider><App /></AuthProvider>, '/oauth/authorize?client_id=client-1&state=s1')
    expect(box.querySelector('[data-testid="mcp-consent"]')).toBeNull()
    expect(box.querySelector('input[type="password"]')).not.toBeNull()
  })

  it('signed in, the consent page shows the program and nextime\'s calendars', async () => {
    serve(server())
    await render(<AuthProvider><App /></AuthProvider>, '/oauth/authorize?client_id=client-1&redirect_uri=http%3A%2F%2F127.0.0.1%3A33418%2Fcallback&response_type=code&code_challenge=abc&code_challenge_method=S256&state=s1')
    const page = box.querySelector('[data-testid="mcp-consent"]')!
    expect(page.textContent).toContain('Probe client')
    // nextime's word for its areas above the choice: its calendars, all or some.
    expect(page.querySelector('[data-testid="consent-areas"]')?.textContent).toContain('Kalender')
    expect(calls.some((call) => call.path.startsWith('/api/oauth/authorize?client_id=client-1'))).toBe(true)
  })

  it('the operator sets programs, the highest level and blocked tools under "MCP-Zugang"', async () => {
    serve(server(me({ role: 'operator' })))
    // E67: the card "Schnittstellen" stands under Server › Verbindungen.
    await render(<AuthProvider><App /></AuthProvider>, '/settings/server-connections')
    const operator = box.querySelector('[data-card="interfaces"] [data-testid="mcp-operator"]')
    expect(operator?.textContent).toContain('Programme dürfen sich verbinden')
  })
})
