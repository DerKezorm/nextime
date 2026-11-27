/**
 * The shared module verbinden in nextime's page Connections: a card for each partner the server names (V20, the
 * module's `in_use`, backend test `test_verbinden_module.py`), and the
 * choices of a connection made by a click stand open in its card (V23: nextasks' areas and lists). Since 0.1.8: only the
 * operator sets the other apps up (`operator` from nextime's account), and the module's pages stand in their own frame.
 */
import App from './App'
import { forgetNextasks } from './features/nextasks/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { PAGE_FRAME } from './vendor/nexverbinden'
import { box, calls, click, me, ok, PARTNER_VIEWS, render, serve, signedIn, start, stop } from './testing'

beforeEach(async () => {
  start()
  forgetNextasks()
  await i18n.changeLanguage('de')
})
afterEach(stop)

const CONNECTED = { connected: true, local_allowed: false, url: 'https://tasks.example.com', host: 'tasks.example.com', name: 'Tester', level: 'read', hidden: false, problem: null, by_connection: true }
const AREAS = { areas: [{ id: 'a1', name: 'Zuhause', on: true, hidden: false, lists: [{ id: 'l1', name: 'Einkauf', on: true }] }] }

function server(nextasksState: string) {
  return signedIn(me(), (_method, path) => {
    if (path === '/api/connect/partners') {
      return ok(PARTNER_VIEWS.map((view) => (view.partner === 'nextasks' ? { ...view, state: nextasksState, host: 'tasks.example.com', address: 'https://tasks.example.com', uses: ['tasks.read', 'tasks.complete'] } : view)))
    }
    if (path === '/api/nextasks') return ok(nextasksState === 'none' ? { connected: false, local_allowed: false } : { ...CONNECTED, by_connection: nextasksState === 'connected' })
    if (path === '/api/nextasks/areas') return ok(AREAS)
    return undefined
  })
}

describe('the other nex apps on the page Connections', () => {
  it('V20 shows a card for each partner the server names (since verbinden 0.1.2 nextime\'s `in_use` there)', async () => {
    serve(server('none'))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/connections')
    const cards = [...box.querySelectorAll('[data-testid^="connection-nex"]')].map((card) => card.getAttribute('data-testid'))
    expect(cards).toEqual(['connection-nextasks', 'connection-nexpaper', 'connection-nexmail', 'connection-nexfolk', 'connection-nexdiary', 'connection-nexnotes'])
  })

  it.each(['connected', 'legacy'])('V23 a connection (%s) shows its choices open in the card, not under "Von Hand verbinden"', async (state) => {
    serve(server(state))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/connections')
    const card = box.querySelector('[data-testid="connection-nextasks"]')!
    const head = card.querySelector<HTMLElement>('[data-part="collapsible-head"]')
    if (head?.getAttribute('aria-expanded') === 'false') await click(head)
    const choices = card.querySelector('[data-testid="nextasks-choices"]')
    expect(choices).not.toBeNull()
    expect(choices!.querySelector('[data-testid="nextasks-read-only"]')).not.toBeNull()
    // Nothing of it twice: the form by hand holds no choice of its own.
    expect(card.querySelectorAll('[data-testid="nextasks-read-only"]')).toHaveLength(1)
  })

  it.each([['operator', true], ['member', false]] as const)('verbinden 0.1.8 a card not set up gives the %s "Einrichten": %s', async (role, shown) => {
    serve(signedIn(me({ role }), (_method, path) => (path === '/api/connect/partners' ? ok(PARTNER_VIEWS.map((view) => ({ ...view, set_up: false }))) : undefined)))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/connections')
    const card = box.querySelector('[data-testid="connection-nexmail"]')!
    const head = card.querySelector<HTMLElement>('[data-part="collapsible-head"]')
    if (head?.getAttribute('aria-expanded') === 'false') await click(head)
    expect(card.querySelector('[data-testid="connection-not-set-up"]')).not.toBeNull()
    expect(card.querySelector('[data-testid="connection-set-up"]')?.getAttribute('href') ?? null).toBe(shown ? '/settings/server-connections#nex-apps' : null)
  })
})

describe('the pages of the module in nextime', () => {
  it('verbinden 0.1.8 (V48) /connect/app stands in the module\'s frame, nextime adds none', async () => {
    const info = {
      app: 'nextime', partner: 'nexmail', partner_host: 'mail.example.com', account: 'anna',
      account_card: { name: 'Anna', username: 'anna', picture: null, color: '#4a6491' },
      partner_may: [{ id: 'calendars.read', level: 'read', areas: true }], app_may: [], areas: [], replaces: false,
    }
    serve(signedIn(me(), (_method, path) => (path.startsWith('/api/connect/consent') ? ok(info) : undefined)))
    await render(<AuthProvider><App /></AuthProvider>, '/connect/app?client_id=https%3A%2F%2Fmail.example.com&redirect_uri=https%3A%2F%2Fmail.example.com%2Fconnect%2Freturn&state=s&code_challenge=c&code_challenge_method=S256')
    const page = box.querySelector<HTMLElement>('[data-testid="connect-consent"]')!
    expect(page.className).toBe(PAGE_FRAME)
    expect(page.parentElement).toBe(box)
    // verbinden 0.1.8 (B1): who is signed in, with nextime's shown name and user name.
    expect(page.querySelector('[data-testid="consent-account-picture"]')).not.toBeNull()
    expect(page.textContent).toContain('Anna')
    expect(page.textContent).toContain('anna')
    // The page asks once, also without a `navigate` of nextime's (verbinden 0.1.9; 0.1.8 asked without end).
    expect(calls.filter((call) => call.path.startsWith('/api/connect/consent'))).toHaveLength(1)
  })

  it('verbinden 0.1.8 (V48) /connect/return stands in the module\'s frame, nextime adds none', async () => {
    // Where a refusal cannot be carried to the card (no sessionStorage), the page says it itself, in its frame.
    const full = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('full')
    })
    serve(signedIn(me(), (_method, path) => (path === '/api/connect/return' ? { status: 409, body: { detail: { code: 'state_unknown', message: 'x' } } } : undefined)))
    await render(<AuthProvider><App /></AuthProvider>, '/connect/return?code=c&state=s')
    full.mockRestore()
    const page = box.querySelector<HTMLElement>('[data-testid="connect-return"]')
    expect(page).not.toBeNull()
    expect(page!.querySelector('[data-testid="return-problem"]')).not.toBeNull()
    expect(page!.className).toBe(PAGE_FRAME)
    expect(page!.parentElement).toBe(box)
  })
})
