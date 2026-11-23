/** nexpaper in the calendar, in the interface: a deadline stands on its day in the month with its kind in front, and a
 * click shows the popover with day, sender, amount and "In nexpaper öffnen" to the document in a new tab; the block
 * under today; "Meine Fristen" in the sidebar with its eye. The server is a stand-in. */
import App from './App'
import { forgetNexpaper, type Deadline, type DeadlineList, type NexpaperStatus } from './features/nexpaper/api'
import { setViewDay } from './features/views/state'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { calls, click, render, settle, start, stop } from './testing'
import { NOW, theWeek, views } from './viewFakes'

const CONNECTED: NexpaperStatus = { connected: true, local_allowed: false, url: 'https://paper.example.com', host: 'paper.example.com', name: 'Robin', version: '1.10.0', hidden: false, problem: null }

const deadline = (id: number, day: string, more: Partial<Deadline> = {}): Deadline => ({
  id: `${id}-due`,
  document_id: id,
  kind: 'due',
  what: 'pay',
  title: 'Rechnung Abrechnung 10/2026',
  sender: 'Stadtwerke',
  amount: 12345,
  currency: 'EUR',
  note: '',
  day,
  status: 'filed',
  link: `https://paper.example.com/dokument/${id}`,
  ...more,
})

function server(deadlines: Deadline[], status: NexpaperStatus = CONNECTED) {
  let now = status
  const list: DeadlineList = { connected: true, hidden: false, problem: null, deadlines }
  views({
    rows: theWeek(),
    more: (method, path, body) => {
      if (path === '/api/nexpaper' && method === 'GET') return { status: 200, body: now }
      if (path === '/api/nexpaper' && method === 'PATCH') {
        now = { ...now, ...(body as Partial<NexpaperStatus>) }
        return { status: 200, body: now }
      }
      if (path.startsWith('/api/deadlines?')) return { status: 200, body: list }
      return undefined
    },
  })
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  forgetNexpaper()
  localStorage.clear()
  setViewDay(null)
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

async function open(where = '/'): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
  await settle()
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)

describe('nexpaper deadlines', () => {
  it('nexpaper: a deadline stands on its day in the month, and its popover opens the document in nexpaper', async () => {
    server([deadline(3, '2026-12-03'), deadline(9, '2026-12-01', { what: 'follow_up', kind: 'follow_up', title: 'Versicherung', note: 'Anrufen', amount: null, status: 'inbox', link: 'https://paper.example.com/eingang/9' })])
    await open('/month')
    const day = document.querySelector('[data-date="2026-12-03"]')!
    expect([...day.querySelectorAll('[data-testid="month-deadline"]')].map((entry) => entry.textContent)).toEqual(['Zahlen: Rechnung Abrechnung 10/2026'])
    expect(document.querySelector('[data-date="2026-12-01"] [data-testid="month-deadline"]')!.textContent).toBe('Wiedervorlage: Versicherung')
    expect(calls.some((call) => call.path.startsWith('/api/deadlines?') && call.path.includes('from='))).toBe(true)

    await click(day.querySelector('[data-testid="deadline-title"]'))
    expect(one('deadline-popover-title')!.textContent).toBe('Rechnung Abrechnung 10/2026')
    expect(one('deadline-popover-when')!.textContent).toContain('Zahlen')
    expect(one('deadline-popover-sender')!.textContent).toBe('von Stadtwerke')
    expect(one('deadline-popover-amount')!.textContent!.replace(/\s/g, ' ')).toBe('123,45 €')
    const link = one('deadline-popover-open')!
    expect(link.textContent).toBe('In nexpaper öffnen')
    expect(link.getAttribute('href')).toBe('https://paper.example.com/dokument/3')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
  })

  it('nexpaper: overdue ones stand under today, "Meine Fristen" counts them and its eye hides them', async () => {
    server([deadline(1, '2026-11-23', { what: 'cancel', kind: 'contract_end', title: 'Handyvertrag' }), deadline(2, '2026-12-10')])
    await open()
    const block = one('deadlines-block')!
    expect(block.querySelector('h2')!.textContent).toBe('Fristen aus nexpaper')
    expect(block.querySelector('[data-testid="deadline-title"]')!.textContent).toBe('Kündigen bis: Handyvertrag')
    expect(block.querySelector('[data-testid="deadline-when"]')!.textContent).toBe('überfällig seit Mo')
    expect(one('deadlines-count')!.textContent).toBe('1')
    await click(one('deadlines-eye'))
    await settle()
    expect(calls.find((call) => call.method === 'PATCH' && call.path === '/api/nexpaper')!.body).toEqual({ hidden: true })
    expect(one('deadlines-block')).toBeNull()
  })

  it('nexpaper: a connected card is folded from the start with "verbunden" and the address in its head, opens and stays open on this device', async () => {
    server([])
    await open('/settings/connections')
    const card = () => document.getElementById('nexpaper')!
    expect(card().dataset.folded).toBe('true')
    expect(card().querySelector('[data-testid="nexpaper-connected"]')).toBeNull()
    expect(card().querySelector('[data-testid="nexpaper-summary"]')!.textContent).toBe('verbundenpaper.example.com')
    await click(card().querySelector<HTMLElement>('[data-testid="card-fold"]'))
    expect(card().querySelector('[data-testid="nexpaper-connected"]')).not.toBeNull()
    expect(card().querySelector('[data-testid="card-fold"]')!.getAttribute('aria-expanded')).toBe('true')
    stop()
    start()
    forgetPrefs()
    server([])
    await open('/settings/connections')
    expect(card().dataset.folded).toBe('false')
  })
})
