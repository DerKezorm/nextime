/** TIME-25 nextasks in the calendar, in the interface: the card under Settings › Connections (how to connect,
 * "verbunden" with the address and what it shows, problems with the way to a new key, "Trennen" after asking); the
 * block "Aufgaben aus nextasks" under today ("heute", "überfällig seit Mo", five and "+n weitere", a quiet line while
 * nextasks is away, a notice when a tick did not arrive); ticking (struck through, a double click is one tap, a second
 * tap takes it back, "wartet", a key that only reads); a task gone; "Meine Aufgaben" in the sidebar with its count and
 * eye; collections; Day and Week; titles as text. The server is a stand-in. */
import { act } from 'react'

import App from './App'
import { forgetNextasks, type AreaChoice, type NextasksStatus, type Task, type TaskList } from './features/nextasks/api'
import { setViewDay } from './features/views/state'
import i18n from './i18n'
import { forgetPrefs, setPref } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, PARTNER_VIEWS, render, settle, start, stop, type } from './testing'
import { NOW, robin, theWeek, views } from './viewFakes'

const CONNECTED: NextasksStatus = { connected: true, local_allowed: false, url: 'https://tasks.example.com', host: 'tasks.example.com', name: 'Robin', level: 'write', hidden: false, problem: null }

const task = (id: string, title: string, day: string, more: Partial<Task> = {}): Task => ({ id, title, day, time: null, link: `https://tasks.example.com/t/${id}`, repeat: '', done: false, waiting: false, ...more })

type Setup = {
  status?: NextasksStatus
  list?: Partial<TaskList>
  tick?: (body: { done: boolean; ref: string }) => { status: number; body: unknown }
  open?: { gone: boolean; link: string }
  connect?: () => { status: number; body: unknown } | undefined
  tree?: AreaChoice[]
  /** The signed-in account's role; robin is a member. */
  role?: 'member' | 'operator'
  collections?: { id: number; name: string; calendar_ids: number[]; with_tasks?: boolean; updated_at: string }[]
}

function server(setup: Setup = {}) {
  let status = setup.status ?? CONNECTED
  const list: TaskList = { connected: true, hidden: false, problem: null, tasks: [], notices: [], level: 'write', ...setup.list }
  let tree: AreaChoice[] = setup.tree ?? []
  views({
    rows: theWeek(),
    collections: setup.collections,
    more: (method, path, body) => {
      if (path === '/api/auth/me' && setup.role) return { status: 200, body: { ...robin, role: setup.role } }
      if (path === '/api/connect/partners') {
        // As the server shows the cards since verbinden 0.1.8: nextasks connected by hand is a connection by API key
        // (its form stays the account's); without one nextasks is not set up here (no address of the operator).
        const tasks = status.connected
          ? { state: 'legacy', address: status.url ?? '', host: status.host ?? '', set_up: true, legacy: { shown: 'ntk_AbCd', expires_at: null } }
          : { set_up: false }
        return { status: 200, body: PARTNER_VIEWS.map((view) => (view.partner === 'nextasks' ? { ...view, ...tasks } : view)) }
      }
      if (path === '/api/nextasks' && method === 'GET') return { status: 200, body: status }
      if (path === '/api/nextasks' && method === 'PUT') {
        const refused = setup.connect?.()
        if (refused) return refused
        status = { ...CONNECTED, url: (body as { url: string }).url }
        return { status: 200, body: status }
      }
      if (path === '/api/nextasks' && method === 'PATCH') {
        status = { ...status, hidden: (body as { hidden: boolean }).hidden }
        return { status: 200, body: status }
      }
      if (path === '/api/nextasks' && method === 'DELETE') {
        status = { connected: false, local_allowed: false }
        return { status: 200, body: null }
      }
      if (path.startsWith('/api/tasks?')) {
        // With a tree, the rows of the sidebar follow the choice as the server builds them.
        const rows = setup.tree
          ? tree.flatMap((area): { id: string; name: string; icon: string; kind: 'area' | 'list'; area_name: string; hidden: boolean }[] => (area.whole ? [{ id: area.id, name: area.name, icon: area.icon, kind: 'area' as const, area_name: '', hidden: false }] : area.lists.filter((one) => one.chosen).map((one) => ({ id: one.id, name: one.name, icon: '', kind: 'list' as const, area_name: area.name, hidden: false }))))
          : list.areas
        return { status: 200, body: { ...list, areas: rows, hidden: Boolean(status.hidden) } }
      }
      const ticked = /^\/api\/tasks\/([\w-]+)\/tick$/.exec(path)
      if (ticked && method === 'POST') {
        const sent = body as { done: boolean; ref: string }
        return setup.tick?.(sent) ?? { status: 200, body: { id: 1, task_id: ticked[1], done: sent.done, state: 'sent', waiting: false, problem: null } }
      }
      if (/^\/api\/tasks\/[\w-]+\/open$/.test(path)) return { status: 200, body: setup.open ?? { gone: false, link: 'https://tasks.example.com/t/x' } }
      if (path.startsWith('/api/tasks/notices/')) return { status: 200, body: null }
      if (path === '/api/nextasks/areas' && method === 'GET') return { status: 200, body: { areas: tree } }
      if (path === '/api/nextasks/areas' && method === 'PUT') {
        const chosen = body as { areas: string[]; lists: string[] }
        tree = tree.map((area) => ({ ...area, whole: chosen.areas.includes(area.id), lists: area.lists.map((one) => ({ ...one, chosen: chosen.areas.includes(area.id) || chosen.lists.includes(one.id) })) }))
        return { status: 200, body: { areas: tree } }
      }
      if (path.startsWith('/api/nextasks/areas/') && method === 'PATCH') {
        const id = path.split('/').pop()
        list.areas = (list.areas ?? []).map((area) => (area.id === id ? { ...area, hidden: (body as { hidden: boolean }).hidden } : area))
        return { status: 200, body: null }
      }
      return undefined
    },
  })
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  forgetNextasks()
  localStorage.clear()
  setViewDay(null)
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
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
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const ticks = () => calls.filter((call) => call.method === 'POST' && /\/tick$/.test(call.path))

/** The card of a connection is folded from the start: open it, as a person would. */
async function unfold(id: string): Promise<void> {
  // The kit's Collapsible card (plan "Einstellungen", Verbindungen 3): folded while `data-open` is false.
  const card = document.querySelector<HTMLElement>(`[data-card="${id}"]`)
  if (card?.dataset.open === 'false') await click(card.querySelector<HTMLElement>('[data-part="collapsible-head"]'))
  // Since the shared module verbinden the card is the module's; nextime's connection typed in by hand stands under
  // "Von Hand verbinden".
  if (!document.querySelector(`[data-testid="${id}-card"]`)) await click(card?.querySelector<HTMLElement>('[data-testid="connection-manual"]'))
}

describe('TIME-25 the connection', () => {
  it('TIME-25 AC-5 not connected: no block, no group in the sidebar, and Connections says how', async () => {
    server({ status: { connected: false, local_allowed: false } })
    await open()
    expect(one('tasks-block')).toBeNull()
    expect(one('calendar-group-nextasks')).toBeNull()
    expect(calls.some((call) => call.path.startsWith('/api/tasks'))).toBe(false)
    // verbinden 0.1.8 (V42, V46): a member connects nothing new by hand; the card says the operator sets nextasks up.
    await open('/settings/connections')
    const card = document.querySelector<HTMLElement>('[data-card="nextasks"]')!
    if (card.dataset.open === 'false') await click(card.querySelector<HTMLElement>('[data-part="collapsible-head"]'))
    expect(one('nextasks-form')).toBeNull()
    expect(one('connection-manual')).toBeNull()
    expect(one('connection-not-set-up')!.textContent).toContain('Server › Verbindungen › Andere nex-Apps')
    stop()
    start()
    forgetNextasks()
    // The operator has the form by hand.
    server({ status: { connected: false, local_allowed: false }, role: 'operator' })
    await open('/settings/connections')
    await unfold('nextasks')
    expect(one('nextasks-how')!.textContent).toContain('in nextasks unter Mein Konto › Verbindungen › API-Schlüssel einen Schlüssel mit „Lesen und schreiben“')
    expect(one('nextasks-local')!.textContent).toContain('Betreiber')
  })

  it('TIME-25 AC-1 EC-6 connecting sends the address without blanks; then "verbunden" with the address and what it shows', async () => {
    server({ status: { connected: false, local_allowed: true }, role: 'operator' })
    await open('/settings/connections')
    await unfold('nextasks')
    const form = one('nextasks-form')!
    const [url, key] = [...form.querySelectorAll<HTMLInputElement>('input')]
    type(url, '  https://tasks.example.com  ')
    type(key, 'ntk_beispiel')
    await click(one('nextasks-connect'))
    await settle()
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/nextasks')!.body).toEqual({ url: 'https://tasks.example.com', token: 'ntk_beispiel' })
    expect(one('nextasks-connected')!.textContent).toBe('verbundentasks.example.com · zeigt deine und dir zugewiesene Aufgaben mit Fälligkeit')
    expect(one('nextasks-form')).toBeNull()
  })

  it('TIME-25 EC-6 operator hint: only the switch stands in the way, the message names it with a link to it', async () => {
    const answers = [
      { status: 422, body: { detail: { code: 'nextasks_address_local', message: 'x' } } },
      { status: 502, body: { detail: { code: 'nextasks_unreachable', message: 'x' } } },
    ]
    server({ status: { connected: false, local_allowed: false }, connect: () => answers.shift(), role: 'operator' })
    await open('/settings/connections')
    await unfold('nextasks')
    const [url, key] = [...one('nextasks-form')!.querySelectorAll<HTMLInputElement>('input')]
    type(url, 'https://nextasks.home.example')
    type(key, 'ntk_beispiel')
    await click(one('nextasks-connect'))
    await settle()
    // verbinden 0.1.8: one place for the own network, the module's card "Andere nex-Apps".
    expect(one('nextasks-form')!.textContent).toContain('nextasks liegt im eigenen Netz. Erlaube das unter Server › Verbindungen › Andere nex-Apps › „Verbindungen ins eigene Netz“.')
    expect(one('nextasks-local-switch')!.getAttribute('href')).toBe('/settings/server-connections#nex-apps')
    await click(one('nextasks-connect'))
    await settle()
    expect(one('nextasks-local-switch')).toBeNull()
  })

  it('TIME-25 EC-4 EC-5 a key run out, one that only reads, another API: Connections says so with the way to a new key', async () => {
    server({ status: { ...CONNECTED, problem: 'nextasks_token_invalid' } })
    await open('/settings/connections')
    await unfold('nextasks')
    expect(one('nextasks-problem')!.textContent).toBe('nextasks nimmt diesen Schlüssel nicht (mehr). Lege in nextasks unter Mein Konto › Verbindungen › API-Schlüssel einen neuen an und trag ihn hier ein.')
    stop()
    start()
    forgetNextasks()
    server({ status: { ...CONNECTED, problem: 'nextasks_too_old' } })
    await open('/settings/connections')
    await unfold('nextasks')
    expect(one('nextasks-problem')!.textContent).toContain('andere Schnittstelle')
    stop()
    start()
    forgetNextasks()
    server({ status: { ...CONNECTED, level: 'read' } })
    await open('/settings/connections')
    await unfold('nextasks')
    expect(one('nextasks-read-only')!.textContent).toContain('darf nur lesen')
  })

  it('TIME-25 AC-8 "Trennen" asks first, then the connection goes', async () => {
    server()
    await open('/settings/connections')
    await unfold('nextasks')
    await click(one('nextasks-disconnect'))
    expect(document.querySelector('[role="dialog"]')!.textContent).toContain('In nextasks bleibt alles, wie es ist.')
    await click(byText('Trennen', document.querySelector<HTMLElement>('[data-testid="nextasks-disconnect-dialog"]')!))
    await settle()
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/nextasks')).toBe(true)
    expect(one('nextasks-form')).not.toBeNull()
  })
})

describe('TIME-25 tasks in Today', () => {
  it('TIME-25 AC-4 AC-6 EC-8 the block under today: "heute", "überfällig seit Mo", five and "+n weitere"', async () => {
    const tasks = [
      task('a', 'Müll rausbringen', '2026-11-26'),
      task('b', 'Steuer', '2026-11-23'),
      ...Array.from({ length: 6 }, (_, index) => task(`c${index}`, `Alt ${index}`, '2026-11-02')),
    ]
    server({ list: { tasks } })
    await open()
    const block = one('tasks-block')!
    expect(block.querySelector('h2')!.textContent).toBe('Aufgaben aus nextasks')
    // Oldest first, as nextasks sorts what is due.
    expect(all('task-row')).toHaveLength(5)
    expect(all('task-row')[0].querySelector('[data-testid="task-when"]')!.textContent).toBe('überfällig seit 2. Nov.')
    expect(one('tasks-more')!.textContent).toBe('+3 weitere')
    await click(one('tasks-more'))
    const rows = all('task-row')
    expect(rows).toHaveLength(8)
    const late = rows[6].querySelector<HTMLElement>('[data-testid="task-when"]')!
    expect(late.textContent).toBe('überfällig seit Mo')
    expect(late.className).toContain('text-warn-500')
    const now = rows[7].querySelector<HTMLElement>('[data-testid="task-when"]')!
    expect(now.textContent).toBe('heute')
    expect(now.className).not.toContain('text-warn-500')
    // Under today's events, in the left column.
    expect(one('today-column')!.compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('TIME-25 AC-3 AC-9 EC-10 ticking: struck through at once, a double click is one tap, a second tap takes it back', async () => {
    server({ list: { tasks: [task('a', 'Müll rausbringen', '2026-11-26')] } })
    await open()
    const box_ = () => one('task-tick') as HTMLInputElement
    await click(box_())
    await click(box_())
    await settle()
    expect(ticks()).toHaveLength(1)
    expect(ticks()[0].body).toMatchObject({ done: true, title: 'Müll rausbringen' })
    expect(box_().getAttribute('aria-checked')).toBe('true')
    expect(one('task-title')!.className).toContain('nt-done')
    vi.setSystemTime(new Date(NOW.getTime() + 2000))
    await click(box_())
    await settle()
    expect(ticks()).toHaveLength(2)
    expect(ticks()[1].body).toMatchObject({ done: false })
    expect((ticks()[0].body as { ref: string }).ref).not.toBe((ticks()[1].body as { ref: string }).ref)
    expect(one('task-title')!.className).not.toContain('nt-done')
  })

  it('TIME-25 EC-3 nextasks away: the tick counts at once and carries "wartet"; a refusal for good says so once', async () => {
    server({
      list: { tasks: [task('a', 'Müll rausbringen', '2026-11-26')], notices: [{ id: 4, title: 'Steuer', problem: 'nextasks_not_found' }] },
      tick: (sent) => ({ status: 200, body: { id: 1, task_id: 'a', done: sent.done, state: 'waiting', waiting: true, problem: null } }),
    })
    await open()
    await click(one('task-tick'))
    await settle()
    expect(one('task-waiting')!.textContent).toBe('wartet')
    expect(one('task-title')!.className).toContain('nt-done')
    expect(one('tasks-notice')!.textContent).toBe('Das Abhaken von „Steuer“ ist in nextasks nicht angekommen.')
    await click(one('tasks-notice-close'))
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/tasks/notices/4/seen')).toBe(true)
  })

  it('TIME-25 EC-4 a key that only reads: the task stays open, with a hint', async () => {
    server({
      list: { tasks: [task('a', 'Müll rausbringen', '2026-11-26')] },
      tick: () => ({ status: 409, body: { detail: { code: 'nextasks_read_only', message: 'x' } } }),
    })
    await open()
    await click(one('task-tick'))
    await settle()
    expect(one('task-tick')!.getAttribute('aria-checked')).toBe('false')
    expect(one('task-note')!.textContent).toContain('darf nur lesen. Die Aufgabe bleibt offen')
  })

  it('TIME-25 EC-7 a click on the title of a task gone says so and takes it out', async () => {
    const windowOpen = vi.fn(() => ({ opener: {}, close: vi.fn(), location: { href: '' } }))
    vi.stubGlobal('open', windowOpen)
    server({ list: { tasks: [task('a', 'Müll rausbringen', '2026-11-26'), task('b', 'Steuer', '2026-11-26')] }, open: { gone: true, link: '' } })
    await open()
    await click(all('task-title')[0])
    await click(one('task-popover-open'))
    await settle()
    expect(one('task-note')!.textContent).toBe('Diese Aufgabe gibt es in nextasks nicht mehr.')
    expect(all('task-title').map((title) => title.textContent)).toEqual(['Steuer'])
    expect(windowOpen).toHaveBeenCalled()
  })

  it('TIME-25 EC-1 nextasks away: a quiet line in the block, the events stand as always', async () => {
    server({ list: { tasks: [], problem: 'nextasks_unreachable' } })
    await open()
    expect(one('tasks-problem')!.textContent).toBe('nextasks ist gerade nicht erreichbar. Die Aufgaben kommen wieder, sobald es antwortet.')
    expect(one('next')!.textContent).toContain('Zahnarzt Ben')
  })

  it('TIME-25 EC-9 EC-15 a title with code stays text; the box is a target of its own apart from the title', async () => {
    server({ list: { tasks: [task('a', '<img src=x onerror=alert(1)>', '2026-11-26')] } })
    await open()
    expect(one('task-title')!.textContent).toBe('<img src=x onerror=alert(1)>')
    expect(one('tasks-block')!.querySelector('img')).toBeNull()
    const tick = one('task-tick')!
    expect(tick.parentElement!.className).toContain('nt-check-hit')
    expect(tick.closest('[data-testid="task-title"]')).toBeNull()
  })
})

describe('TIME-25 tasks on a phone', () => {
  it('TIME-25 AC-6 at phone width the tasks stand in the list of today with "Aufgabe"', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('699'), media: query, addEventListener: () => undefined, removeEventListener: () => undefined }))
    server({ list: { tasks: [task('a', 'Müll rausbringen', '2026-11-26')] } })
    await open()
    expect(one('tasks-block')!.getAttribute('data-phone')).toBe('true')
    expect(one('tasks-block')!.className).not.toContain('rounded-[12px]')
    expect(one('task-when')!.textContent).toBe('Aufgabe · heute')
  })
})

describe('TIME-25 my tasks like a calendar', () => {
  it('TIME-25 AC-7 "Meine Aufgaben" under "nextasks" with the count of today and overdue; the eye hides the tasks', async () => {
    server({ list: { tasks: [task('a', 'Müll', '2026-11-26'), task('b', 'Steuer', '2026-11-23'), task('c', 'Später', '2026-11-28')] } })
    await open()
    const group = one('calendar-group-nextasks')!
    expect(group.textContent).toContain('nextasks')
    expect(group.textContent).toContain('Meine Aufgaben')
    expect(one('my-tasks-count')!.textContent).toBe('2')
    await click(one('task-area-eye'))
    await settle()
    expect(calls.find((call) => call.method === 'PATCH' && call.path === '/api/nextasks')!.body).toEqual({ hidden: true })
    expect(one('tasks-block')).toBeNull()
    expect(one('my-tasks-count')).toBeNull()
  })

  it('TIME-25 AC-7 in a collection only with "Meine Aufgaben"; with one calendar alone none', async () => {
    setPref('collection', 1, '9')
    server({ list: { tasks: [task('a', 'Müll', '2026-11-26')] }, collections: [{ id: 9, name: 'Familie', calendar_ids: [1], with_tasks: false, updated_at: '' }] })
    await open()
    expect(one('tasks-block')).toBeNull()
    stop()
    start()
    forgetNextasks()
    setPref('collection', 1, '9')
    server({ list: { tasks: [task('a', 'Müll', '2026-11-26')] }, collections: [{ id: 9, name: 'Familie', calendar_ids: [1], with_tasks: true, updated_at: '' }] })
    await open()
    expect(one('tasks-block')).not.toBeNull()
    stop()
    start()
    forgetNextasks()
    setPref('collection', 1, null)
    setPref('only', 1, '2')
    server({ list: { tasks: [task('a', 'Müll', '2026-11-26')] } })
    await open()
    expect(one('tasks-block')).toBeNull()
  })

  it('TIME-25 AC-7 the collection dialog offers "Meine Aufgaben" with the source nextasks and saves it', async () => {
    server()
    await open()
    await click(one('collection-button'))
    await click(one('collection-new'))
    const dialog = one('collection-dialog')!
    const choice = dialog.querySelector<HTMLInputElement>('[data-testid="collection-tasks"]')!
    expect(choice.closest('label')!.textContent).toBe('Meine Aufgabennextasks')
    type(dialog.querySelector<HTMLInputElement>('[data-testid="collection-name"]')!, 'Alltag')
    await click(choice)
    await click(dialog.querySelector('button[type="submit"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/collections')!.body).toMatchObject({ name: 'Alltag', task_areas: ['*'] })
  })
})

describe('TIME-25 Day and Week', () => {
  it('TIME-25 AC-2 in the week a task without a time stands in the all-day row, one with a time at its place, dashed', async () => {
    server({ list: { tasks: [task('a', 'Geschenk Oma', '2026-11-27'), task('b', 'Anrufen', '2026-11-28', { time: '14:30' })] } })
    await open('/week')
    const cell = document.querySelector<HTMLElement>('[data-all-day="2026-11-27"]')!
    expect(cell.querySelector('[data-testid="task-chip"]')!.textContent).toBe('Geschenk Oma')
    const column = document.querySelector<HTMLElement>('[data-testid="grid-column"][data-date="2026-11-28"]')!
    const timed = column.querySelector<HTMLElement>('[data-testid="task-chip"]')!
    expect(timed.className).toContain('nt-task-timed')
    expect(Number(timed.style.getPropertyValue('--s'))).toBeCloseTo(14.5)
    expect(timed.className).toContain('nt-task-chip')
    await act(async () => undefined)
  })
})

describe('TIME-25 asked for while testing (2026-11-23)', () => {
  const AREAS = [
    { id: 's-home', name: 'Zuhause', icon: 'home', color: '', kind: 'area' as const, area_name: '', link: 'https://tasks.example.com', hidden: false },
    { id: 'l-one', name: 'Büro', icon: 'briefcase', color: 'green', kind: 'list' as const, area_name: 'Arbeit', link: 'https://tasks.example.com/lists/l-one', hidden: false },
  ]
  const TREE: AreaChoice[] = [
    { id: 's-home', name: 'Zuhause', icon: 'home', whole: true, lists: [{ id: 'l-home', name: 'Haus', chosen: true }] },
    { id: 's-work', name: 'Arbeit', icon: '', whole: true, lists: [{ id: 'l-one', name: 'Büro', chosen: true }, { id: 'l-two', name: 'Reisen', chosen: true }] },
  ]

  it('TIME-25 month later repeating: tasks in their day after the events, a preview with its mark and no box, the shown window asked', async () => {
    server({ list: { tasks: [task('a', 'Müll rausbringen', '2026-12-03'), task('r', 'Pflanzen gießen', '2026-12-01', { repeat: 'FREQ=WEEKLY', preview: true })] } })
    await open('/month')
    const day = document.querySelector('[data-date="2026-12-03"]')!
    expect([...day.querySelectorAll('[data-testid="month-task"]')].map((entry) => entry.textContent)).toEqual(['Müll rausbringen'])
    const preview = document.querySelector('[data-date="2026-12-01"] [data-testid="month-task"]')!
    expect(preview.querySelector('[data-testid="task-preview-mark"]')).not.toBeNull()
    expect(preview.querySelector('[data-testid="task-tick"]')).toBeNull()
    expect(calls.some((call) => call.path.startsWith('/api/tasks?') && call.path.includes('from=') && call.path.includes('previews=true'))).toBe(true)
  })

  it('TIME-25 column: a coming task among the next events, with a box and "Aufgabe · in 3 Tagen · wöchentlich"', async () => {
    server({ list: { tasks: [task('c', 'Steuer abgeben', '2026-11-29', { repeat: 'FREQ=WEEKLY' })] } })
    await open()
    const row = one('next-events')!.querySelector<HTMLElement>('[data-testid="column-task"]')!
    expect(row.querySelector('[data-testid="task-tick"]')).not.toBeNull()
    expect(row.querySelector('[data-testid="column-sub"]')!.textContent).toBe('Aufgabe · in 3 Tagen · wöchentlich')
  })

  it('TIME-25 popover: a click on a task shows area, due day, rule, who, the note and "In nextasks öffnen"; Escape closes', async () => {
    server({ list: { tasks: [task('a', 'Müll rausbringen', '2026-11-23', { area: 's-home', area_name: 'Zuhause', section: 'Garten', repeat: 'FREQ=WEEKLY', assignees: ['Robin'], notes: 'Gelbe Tonne' })] } })
    await open()
    await click(all('task-title')[0])
    const popover = one('task-popover')!
    expect(one('task-popover-place')!.textContent).toBe('Zuhause › Garten')
    expect(one('task-popover-when')!.textContent).toContain('überfällig seit')
    expect(one('task-popover-rule')!.textContent).toBe('wiederholt sich wöchentlich')
    expect(one('task-popover-assigned')!.textContent).toBe('zugewiesen an Robin')
    expect(one('task-popover-notes')!.textContent).toBe('Gelbe Tonne')
    expect(one('task-popover-open')!.getAttribute('href')).toBe('https://tasks.example.com/t/a')
    expect(popover.querySelector('[data-testid="task-tick"]')).not.toBeNull()
    await act(async () => {
      popover.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(one('task-popover')).toBeNull()
  })

  it('TIME-25 area rows: an area whole and a list alone, with symbol and colour of nextasks; the eye hides one; a click shows it alone', async () => {
    server({ list: { areas: AREAS, tasks: [task('h', 'Rasen mähen', '2026-11-26', { row: 's-home' }), task('w', 'Bericht', '2026-11-26', { row: 'l-one', color: 'green' })] } })
    await open()
    expect(all('task-area').map((row) => row.textContent)).toEqual(['Zuhause1', 'Büro · Arbeit1'])
    const marks = all('task-area').map((row) => row.querySelector('[data-testid="area-mark"]')!)
    expect(marks.map((mark) => [mark.getAttribute('data-symbol'), mark.getAttribute('data-color')])).toEqual([['home', null], ['briefcase', 'green']])
    expect((marks[1] as unknown as SVGElement).style.color).toBe('rgb(79, 158, 63)')
    await click(document.querySelector('[data-area="l-one"] [data-testid="task-area-eye"]'))
    await settle()
    expect(calls.find((call) => call.method === 'PATCH' && call.path === '/api/nextasks/areas/l-one')!.body).toEqual({ hidden: true })
    expect(all('task-title').map((title) => title.textContent)).toEqual(['Rasen mähen'])
    // Shown alone in the view on screen: from Heute it stays Heute, in the month it stays the month.
    await click(document.querySelector('[data-area="s-home"] [data-testid="task-area-name"]'))
    await settle()
    expect(one('view-today')).not.toBeNull()
    expect(one('only-bar')!.textContent).toContain('Zuhause')
    await click(box.querySelector('[data-testid="sidebar-places"] a[href="/month"]'))
    await settle()
    expect(one('view-month')).not.toBeNull()
    expect(one('only-bar')!.textContent).toContain('Zuhause')
    expect(all('month-event')).toEqual([])
    expect(all('month-task').map((entry) => entry.textContent)).toEqual(['Rasen mähen'])
  })

  it('TIME-25 areas: Connections shows areas with their lists; an area whole locks its lists, single lists leave it half', async () => {
    server({ list: { areas: AREAS }, tree: TREE })
    await open('/settings/connections')
    await unfold('nextasks')
    const boxes = () => all('nextasks-area') as HTMLInputElement[]
    const lists = () => all('nextasks-list') as HTMLInputElement[]
    expect(boxes().map((box) => box.checked)).toEqual([true, true])
    expect(lists().map((box) => [box.checked, box.disabled])).toEqual([[true, true], [true, true], [true, true]])
    await click(boxes()[1])
    await click(lists()[1])
    expect(calls.filter((call) => call.method === 'PUT' && call.path === '/api/nextasks/areas').at(-1)!.body).toEqual({ areas: ['s-home'], lists: ['l-one'] })
    expect(boxes()[1].indeterminate).toBe(true)
    expect(lists().map((box) => [box.checked, box.disabled])).toEqual([[true, true], [true, false], [false, false]])
  })

  it('TIME-25 popover close: the × and a click beside it close it; that click starts no new event in the month', async () => {
    server({ list: { tasks: [task('a', 'Bänder', '2026-12-03')] } })
    await open('/month')
    await click(all('task-title')[0])
    await click(one('task-popover-close'))
    expect(one('task-popover')).toBeNull()
    await click(all('task-title')[0])
    const free = document.querySelector<HTMLElement>('[data-testid="month-day"][data-date="2026-12-02"]')!
    await act(async () => {
      free.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true }))
      free.click()
    })
    await settle()
    expect(one('task-popover')).toBeNull()
    expect(one('event-panel')).toBeNull()
  })

  it('TIME-25 done: "Erledigt" ticks the task, struck through; "Nicht erledigt" takes it back', async () => {
    server({ list: { tasks: [task('a', 'Bänder', '2026-12-03')] } })
    await open('/month')
    await click(all('task-title')[0])
    expect(one('task-popover-done')!.textContent).toBe('Erledigt')
    await click(one('task-popover-done'))
    expect(ticks().at(-1)!.body).toMatchObject({ done: true })
    expect(all('task-title')[0].className).toContain('nt-done')
    expect(one('task-popover-done')!.textContent).toBe('Nicht erledigt')
    vi.setSystemTime(Date.now() + 1000)
    await click(one('task-popover-done'))
    expect(ticks().at(-1)!.body).toMatchObject({ done: false })
    expect(all('task-title')[0].className).not.toContain('nt-done')
  })

  it('TIME-25 area rows: an area in part with two lists and one whole give three rows, at once after choosing', async () => {
    const tree: AreaChoice[] = [
      { id: 's-private', name: 'Privat', icon: '', whole: true, lists: [{ id: 'l-general', name: 'Allgemein', chosen: true }, { id: 'l-ha', name: 'HA Wartung', chosen: true }, { id: 'l-work', name: 'Arbeit; ZA31', chosen: true }] },
      { id: 's-family', name: 'Familie', icon: '', whole: true, lists: [{ id: 'l-shop', name: 'Einkauf', chosen: true }] },
    ]
    server({ tree })
    await open('/settings/connections')
    await unfold('nextasks')
    await settle()
    const boxes = () => all('nextasks-area') as HTMLInputElement[]
    const lists = () => all('nextasks-list') as HTMLInputElement[]
    await click(boxes()[0])
    await click(lists()[0])
    await click(lists()[1])
    await settle()
    expect(all('task-area').map((row) => row.textContent)).toEqual(['Allgemein · Privat', 'HA Wartung · Privat', 'Familie'])
  })
})
