/**
 * The shared module verbinden in nextime's page Connections: only partners nextime has a use for get a card (V20:
 * nextasks and nexpaper as their guest, nexfolk as their guest for birthdays, nexmail and nexdiary as their host; nexnotes not yet), and the
 * choices of a connection made by a click stand open in its card (V23: nextasks' areas and lists).
 */
import App from './App'
import { forgetNextasks } from './features/nextasks/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, click, me, ok, PARTNER_VIEWS, render, serve, signedIn, start, stop } from './testing'

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
    if (path === '/api/nextasks') return ok(nextasksState === 'connected' ? CONNECTED : { connected: false, local_allowed: false })
    if (path === '/api/nextasks/areas') return ok(AREAS)
    return undefined
  })
}

describe('the other nex apps on the page Connections', () => {
  it('V20 shows a card only for partners nextime has a use for', async () => {
    serve(server('none'))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/connections')
    const cards = [...box.querySelectorAll('[data-testid^="connection-nex"]')].map((card) => card.getAttribute('data-testid'))
    expect(cards).toEqual(['connection-nextasks', 'connection-nexpaper', 'connection-nexmail', 'connection-nexfolk', 'connection-nexdiary'])
  })

  it('V23 a connection made by a click shows its choices open in the card', async () => {
    serve(server('connected'))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/connections')
    const card = box.querySelector('[data-testid="connection-nextasks"]')!
    const head = card.querySelector<HTMLElement>('[data-part="collapsible-head"]')
    if (head?.getAttribute('aria-expanded') === 'false') await click(head)
    const choices = card.querySelector('[data-testid="nextasks-choices"]')
    expect(choices).not.toBeNull()
    expect(choices!.querySelector('[data-testid="nextasks-read-only"]')).not.toBeNull()
  })
})
