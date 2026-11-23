/** TIME-28 Phone and installable web app: the bar at the bottom and the head below 1024 px, the profile with what the
 * sidebar holds, the week as three days, reading the kept four weeks without a connection (the orange hint, everything
 * that changes something off, the period past them, the search in them, the side panel), the connection coming back,
 * a server too slow, saving when the connection breaks, signing out and another account, too little room, a session
 * that ran out meanwhile, a new version, the return over midnight. Now is Thursday, 26 November 2026, 16:05 in Berlin;
 * the IndexedDB of the browser is a stand-in in memory (jsdom has none). */
import { act } from 'react'

import App from './App'
import type { Occurrence } from './features/calendars/api'
import { followAccount, forgetAccount } from './state/account'
import { isOffline, markReachable, markUnreachable, probing, resetConnection, watchSlow } from './features/offline/connection'
import { forgetTaking } from './features/offline/take'
import { fold, keptSearch } from './features/offline/kept'
import { keep, readKept, setShelf, type Shelf, type Snapshot } from './features/offline/snapshot'
import { setViewDay } from './features/views/state'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { setPwaState } from './pwa/worker'
import { AuthProvider, PENDING_SIGN_OUT } from './state/auth'
import { box, byText, calls, click, ok, render, settle, start, stop, type } from './testing'
import { at, BERLIN, NOW, robin, theWeek, views, type Fake } from './viewFakes'

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const text = (id: string) => one(id)?.textContent ?? ''
const wait = (ms: number) => act(() => new Promise<void>((resolve) => setTimeout(resolve, ms)))

/** IndexedDB as a stand-in: what is put, and whether it fails. */
function memoryShelf(): Shelf & { value: unknown; fail: boolean; cleared: number } {
  const shelf = {
    value: undefined as unknown,
    fail: false,
    cleared: 0,
    get: async () => shelf.value,
    put: async (value: Snapshot) => {
      if (shelf.fail) throw new DOMException('The quota has been exceeded.', 'QuotaExceededError')
      shelf.value = JSON.parse(JSON.stringify(value))
    },
    clear: async () => {
      shelf.cleared += 1
      shelf.value = undefined
    },
  }
  return shelf
}

let shelf: ReturnType<typeof memoryShelf>
/** The network: down, every request fails as without a connection. */
let down = false

/** The week of the screens, an event in week 2 with notes, and one in week 6 past what is kept. */
function rows(): Occurrence[] {
  return [
    ...theWeek(),
    at(1, 'Zahnarzt Kontrolle', '2026-12-08 09:00', '2026-12-08 09:30'),
    at(1, 'Zahnarzt Jahrescheck', '2027-01-07 09:00', '2027-01-07 09:30'),
  ]
}

const TRASH = { now: '2026-11-26T15:05:00Z', trash_days: 30, warn_days: 7, total: 1, offset: 0, items: [
  { kind: 'event', id: 5, title: 'Alt', color: 'slate', deleted_at: '2026-11-25T10:00:00Z', expires_at: '2026-12-25T10:00:00Z', days_left: 29, warn: false, deleted_by: { self: true, name: '' },
    calendar: { id: 1, name: 'Familie', source: '' }, calendar_gone: false, all_day: false, start: '2026-11-20T10:00:00Z', time_zone: 'Europe/Berlin', recurring: false, exceptions: false },
] }

const NOTES: Record<string, string> = { 'Zahnarzt Ben': 'Versichertenkarte mitnehmen', 'Werkstatt': 'Winterreifen' }

/** The server of the views, with `/api/offline` as the server gives it: the window asked, with notes, "busy only"
 * without any. */
function fake(change: Partial<Fake> = {}): Fake {
  const list = change.rows ?? rows()
  return {
    rows: list,
    ...change,
    more: (method, path, body) => {
      const extra = change.more?.(method, path, body)
      if (extra) return extra
      if (path.startsWith('/api/trash')) return ok(TRASH)
      if (path.startsWith('/api/offline?')) {
        const query = new URL(path, 'http://x').searchParams
        const begin = Date.parse(query.get('start')!)
        const end = Date.parse(query.get('end')!)
        const inside = list.filter((row) => (row.all_day ? Date.parse(row.start) < end && Date.parse(row.end) + 86_400_000 > begin : Date.parse(row.start) < end && Date.parse(row.end) > begin))
        return ok({ taken_at: NOW.toISOString(), start: query.get('start'), end: query.get('end'), occurrences: inside.map((row) => ({ ...row, notes: row.busy_only ? '' : (NOTES[row.title] ?? '') })) })
      }
      return undefined
    },
  }
}

async function open(where: string, server: Fake = fake()): Promise<void> {
  views(server)
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
  // The snapshot is taken right after the start.
  await wait(5)
  await settle()
}

async function goOffline(): Promise<void> {
  down = true
  await act(async () => void window.dispatchEvent(new Event('offline')))
  await settle()
}

async function comeBack(): Promise<void> {
  down = false
  await act(async () => void window.dispatchEvent(new Event('online')))
  await wait(5)
  await settle()
}

/** The server with one open invitation. */
function invited(): Fake {
  return fake({ more: (_method, path) => (path === '/api/invitations' ? ok([{ id: 9, occurrence: null, title: 'Elternabend', start: '2026-11-30T17:00:00Z', end: '2026-11-30T18:00:00Z', all_day: false, by: 'Schule', calendar_id: 1 }]) : undefined) })
}

function compact(narrow = false): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('1023') || (narrow && query.includes('699')),
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  // Over the stand-in server of `testing.tsx`: without a connection every request fails as the browser's fetch does.
  const served = globalThis.fetch
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (down) throw new TypeError('Failed to fetch')
      return served(input, init)
    }),
  )
  down = false
  forgetPrefs()
  forgetAccount()
  localStorage.clear()
  setViewDay(null)
  shelf = memoryShelf()
  setShelf(shelf)
  resetConnection()
  forgetTaking()
  setPwaState({ standalone: true, update: false })
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  resetConnection()
  setShelf(null)
  setPwaState({ standalone: false, update: false })
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('TIME-28 the phone and the tablet', () => {
  it('TIME-28 AC-5 below 1024 px the bar at the bottom has Heute, Woche, the round plus, Monat and Suchen; the view shown is marked', async () => {
    compact()
    await open('/month')
    const bar = one('bottom-bar')!
    expect(bar.className).toContain('lg:hidden')
    const entries = [...bar.querySelectorAll<HTMLElement>('a, button')]
    expect(entries.map((entry) => entry.textContent?.trim() || entry.getAttribute('aria-label'))).toEqual(['Heute', 'Woche', 'Neuer Termin', 'Monat', 'Suchen'])
    expect(entries[2].tagName).toBe('BUTTON')
    expect(entries[2].className).toContain('rounded-full')
    expect(one('bottom-month')!.getAttribute('aria-current')).toBe('page')
    expect(one('bottom-month')!.className).toContain('nt-bar-current')
    expect(one('bottom-today')!.getAttribute('aria-current')).toBeNull()
    await click(one('bottom-plus'))
    expect(one('event-panel')).not.toBeNull()
  })

  it('TIME-28 AC-1 EC-8 the frame never runs wider than the screen, and the bar wraps its words instead of cutting them', async () => {
    compact()
    await open('/')
    const frame = box.querySelector<HTMLElement>('.h-dvh')!
    expect(frame.className).toContain('overflow-hidden')
    expect(one('bottom-bar')!.parentElement!.className).toContain('min-w-0')
    for (const entry of one('bottom-bar')!.querySelectorAll('a')) {
      expect(entry.className).toContain('min-w-0')
      expect(entry.querySelector('.nt-wrap')).not.toBeNull()
      expect(entry.className).not.toContain('truncate')
    }
    expect(one('bottom-bar')!.className).toContain('pb-[env(safe-area-inset-bottom)]')
  })

  it('TIME-28 AC-6 below 1024 px the head has the mark, the collection, the invitation and the profile; "Schnell eintragen" below', async () => {
    compact()
    await open('/', invited())
    const head = one('compact-head')!
    expect(head.className).toContain('lg:hidden')
    expect(head.querySelector('[data-testid="logo-mark"]')).not.toBeNull()
    expect(head.textContent).toContain('Alles')
    expect(head.querySelector('[data-testid="invitation-sign"]')?.textContent).toBe('1 Einladung')
    expect(head.querySelector('[data-testid="profile-button"]')).not.toBeNull()
    // Once only: not beside "Schnell eintragen" as on a wide screen.
    expect(all('invitation-sign')).toHaveLength(1)
    expect(one('quick-entry')).not.toBeNull()
  })

  it('TIME-28 AC-7 below 700 px the week shows three days side by side', async () => {
    compact(true)
    await open('/week')
    expect(all('grid-day-head').length || box.querySelectorAll('.nt-grid-head > :not(:first-child)').length).toBe(3)
  })

  it('TIME-28 AC-8 the profile at the top right holds the calendar list with "Kalender hinzufügen", the trash and the settings', async () => {
    compact()
    await open('/')
    await click(one('profile-button'))
    const sheet = one('profile-sheet')!
    expect(sheet.className).toContain('lg:hidden')
    expect(sheet.querySelector('[data-testid="add-calendar"]')).not.toBeNull()
    expect(sheet.textContent).toContain('Familie')
    expect(sheet.querySelector('[data-testid="trash-entry"]')).not.toBeNull()
    expect(sheet.querySelector('[data-testid="settings-entry"]')?.getAttribute('aria-label')).toBe('Robin · Einstellungen')
    await click(sheet.querySelector('[data-testid="trash-entry"]'))
    expect(one('profile-sheet')).toBeNull()
    expect(one('trash-page')).not.toBeNull()
  })

  it('TIME-28 EC-10 from 1024 px the sidebar, below it the head and the bar; turning the tablet switches by itself', async () => {
    let upright = false
    const changes = new Set<() => void>()
    vi.stubGlobal('matchMedia', (query: string) => ({
      get matches() {
        return query.includes('1023') && upright
      },
      addEventListener: (_type: string, listener: () => void) => changes.add(listener),
      removeEventListener: (_type: string, listener: () => void) => changes.delete(listener),
    }))
    await open('/', invited())
    // The edges are Tailwind's `lg` (1024 px), the same as the media query of the frame.
    expect(one('sidebar-wide')!.className).toContain('hidden')
    expect(one('sidebar-wide')!.className).toContain('lg:flex')
    expect(one('compact-head')!.className).toContain('lg:hidden')
    expect(one('bottom-bar')!.className).toContain('lg:hidden')
    // Across (1024 × 768): the invitation beside "Schnell eintragen"; upright (768 × 1024): in the head.
    const { COMPACT } = await import('./features/views/state')
    expect(COMPACT).toBe('(max-width: 1023px)')
    expect(one('view-today')!.contains(one('invitation-sign'))).toBe(true)
    upright = true
    await act(async () => changes.forEach((listener) => listener()))
    await settle()
    expect(one('compact-head')!.contains(one('invitation-sign'))).toBe(true)
    expect(all('invitation-sign')).toHaveLength(1)
    upright = false
    await act(async () => changes.forEach((listener) => listener()))
    await settle()
    expect(one('view-today')!.contains(one('invitation-sign'))).toBe(true)
  })
})

describe('TIME-28 without a connection', () => {
  it('TIME-28 AC-3 the installed app reads the kept 4 weeks, says so in orange and turns off all that changes something', async () => {
    await open('/')
    expect(calls.some((call) => call.path.startsWith('/api/offline?'))).toBe(true)
    expect(((shelf.value as Snapshot).occurrences ?? []).map((row) => row.title)).toContain('Zahnarzt Kontrolle')
    expect(((shelf.value as Snapshot).occurrences ?? []).map((row) => row.title)).not.toContain('Zahnarzt Jahrescheck')
    await goOffline()
    expect(text('offline-state')).toBe('Ohne Verbindung · Stand von heute 16:05 · nur lesen')
    expect(text('offline-text')).toBe('Die nächsten 4 Wochen bleiben lesbar. Eintragen geht wieder mit Verbindung, oder später mit der nextime-App fürs iPhone.')
    expect(one('offline-hint')!.className).toContain('warn')
    // Still readable, from what is kept.
    expect(box.textContent).toContain('Zahnarzt Ben')
    // Everything that changes something is off.
    expect((one('bottom-plus') as HTMLButtonElement).disabled).toBe(true)
    expect(one('bottom-plus')!.getAttribute('aria-label')).toBe('Neuer Termin, ohne Verbindung nicht möglich')
    expect((one('new-event') as HTMLButtonElement).disabled).toBe(true)
    expect((one('quick-entry') as HTMLInputElement).disabled).toBe(true)
    expect((one('add-calendar') as HTMLButtonElement).disabled).toBe(true)
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'n' })))
    await settle()
    expect(one('event-panel')).toBeNull()
    // The side panel reads the event from what is kept, to read only.
    const before = calls.length
    await click([...box.querySelectorAll<HTMLElement>('button')].find((button) => button.textContent?.includes('Zahnarzt Ben')))
    expect(one('offline-panel')).not.toBeNull()
    expect(text('offline-panel')).toContain('Zahnarzt Ben')
    expect(text('offline-notes')).toBe('Versichertenkarte mitnehmen')
    expect(text('read-only-reason')).toBe('Ohne Verbindung nur lesen. Ändern geht wieder mit Verbindung.')
    expect(one('panel-save')).toBeNull()
    expect(calls.slice(before).filter((call) => call.method !== 'GET')).toEqual([])
  })

  it('TIME-28 AC-3 dragging in the grid moves nothing without a connection and says why; a click into free room opens nothing', async () => {
    await open('/week')
    await goOffline()
    const block = box.querySelector<HTMLElement>('[data-event]')!
    const pointer = async (target: Element, kind: string, y: number) => act(async () => void target.dispatchEvent(new MouseEvent(kind, { bubbles: true, clientX: 50, clientY: y, button: 0 })))
    await pointer(block, 'pointerdown', 10)
    await pointer(block, 'pointermove', 46)
    await pointer(block, 'pointerup', 46)
    await settle()
    expect(calls.filter((call) => call.method === 'PATCH')).toEqual([])
    expect(box.textContent).toContain('Ohne Verbindung lässt sich nichts verschieben.')
  })

  it('TIME-28 AC-3 the trash restores nothing without a connection', async () => {
    await open('/trash')
    expect((one('trash-restore') as HTMLButtonElement).disabled).toBe(false)
    await goOffline()
    expect(all('trash-restore')).toHaveLength(1)
    for (const button of all('trash-restore')) expect((button as HTMLButtonElement).disabled).toBe(true)
    expect((one('trash-empty') as HTMLButtonElement).disabled).toBe(true)
  })

  it('TIME-28 AC-4 when the connection comes back, nextime fetches anew by itself and the hint goes', async () => {
    await open('/week')
    await goOffline()
    expect(one('offline-hint')).not.toBeNull()
    const before = calls.length
    await comeBack()
    expect(isOffline()).toBe(false)
    expect(one('offline-hint')).toBeNull()
    const after = calls.slice(before).map((call) => call.path)
    expect(after.some((path) => path === '/api/health')).toBe(true)
    expect(after.some((path) => path.startsWith('/api/events?'))).toBe(true)
    // Not only the views: the calendar list and the rest ask anew too.
    expect(after.some((path) => path === '/api/calendars')).toBe(true)
    expect((one('new-event') as HTMLButtonElement).disabled).toBe(false)
  })

  it('TIME-28 EC-3 a period past the kept 4 weeks says it shows with a connection only, instead of an empty, free week', async () => {
    await open('/week')
    await goOffline()
    expect(one('offline-outside')).toBeNull()
    setViewDay('2027-01-07')
    await settle()
    expect(text('offline-outside')).toBe('Dieser Zeitraum erscheint erst mit Verbindung.')
    expect(box.textContent).not.toContain('Zahnarzt Jahrescheck')
    setViewDay('2026-12-08')
    await settle()
    expect(one('offline-outside')).toBeNull()
    expect(box.textContent).toContain('Zahnarzt Kontrolle')
  })

  it('TIME-28 EC-4 a server down (502 to 504, no answer to the health question) counts as no connection', async () => {
    let failing = false
    const server = fake({
      more: (_method, path) => (failing && (path.startsWith('/api/events?') || path === '/api/health') ? { status: 503, body: { detail: { code: 'internal_error' } } } : undefined),
    })
    await open('/week', server)
    expect(isOffline()).toBe(false)
    failing = true
    setViewDay('2026-12-03')
    await settle()
    expect(isOffline()).toBe(true)
    expect(text('offline-state')).toBe('Ohne Verbindung · Stand von heute 16:05 · nur lesen')
    failing = false
    await act(async () => markReachable())
    await settle()
    expect(isOffline()).toBe(false)
    // Too slow and the health question unanswered too: without a connection.
    failing = true
    const never = new Promise<never>(() => undefined)
    void watchSlow(never, 20).catch(() => undefined)
    await wait(60)
    expect(isOffline()).toBe(true)
  })

  it('TIME-28 EC-4 AC-4 a slow view of a server that answers its health question is no reason to go offline: no flapping', async () => {
    await open('/week')
    const before = calls.length
    const never = new Promise<never>(() => undefined)
    void watchSlow(never, 20).catch(() => undefined)
    await wait(60)
    await settle()
    expect(isOffline()).toBe(false)
    expect(one('offline-hint')).toBeNull()
    // One health question, not every view again.
    const after = calls.slice(before).map((call) => call.path)
    expect(after.filter((path) => path === '/api/health')).toHaveLength(1)
    expect(after.filter((path) => path.startsWith('/api/events?'))).toHaveLength(0)
  })

  it('TIME-28 EC-5 a save cut off says so, keeps what was typed and makes nothing twice on the next try', async () => {
    await open('/week')
    await click(one('new-event'))
    type(one('field-title') as HTMLInputElement, 'Elternabend')
    down = true
    await click(one('panel-save'))
    expect(box.textContent).toContain('Keine Verbindung zu nextime. Deine Eingaben bleiben stehen; sichere gleich noch einmal.')
    expect((one('field-title') as HTMLInputElement).value).toBe('Elternabend')
    down = false
    await click(one('panel-save'))
    const posts = calls.filter((call) => call.method === 'POST' && call.path === '/api/events')
    expect(posts).toHaveLength(1)
    // The cut-off one never reached the server; had it, the same reference makes the server give back the first.
    const first = (calls.filter((call) => call.path === '/api/events' && call.method === 'POST')[0].body as { client_ref: string }).client_ref
    expect(first).toMatch(/.+/)
    await click(one('new-event'))
    type(one('field-title') as HTMLInputElement, 'Zweiter')
    down = true
    await click(one('panel-save'))
    down = false
    await click(one('panel-save'))
    const refs = calls.filter((call) => call.method === 'POST' && call.path === '/api/events').map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs).toHaveLength(2)
    expect(refs[1]).not.toBe(first)
  })

  it('TIME-28 EC-11 the search without a connection looks in the kept 4 weeks only and says so; "busy only" is never found', async () => {
    await open('/')
    await goOffline()
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true })))
    await settle()
    type(one('search-field') as HTMLInputElement, 'zahnarzt')
    await wait(200)
    await settle()
    const titles = all('search-hit-title').map((hit) => hit.textContent)
    // Week 1 and week 2 are kept, week 6 is not.
    expect(titles).toEqual(['Zahnarzt Ben', 'Zahnarzt Kontrolle'])
    expect(text('search-count')).toContain('ohne Verbindung nur in den gespeicherten 4 Wochen')
    type(one('search-field') as HTMLInputElement, 'winterreifen')
    await wait(200)
    await settle()
    expect(all('search-hit-title').map((hit) => hit.textContent)).toEqual(['Werkstatt'])
    // The search field asks nobody; only the list of next events was asked while online.
    expect(calls.some((call) => call.path.startsWith('/api/search?'))).toBe(false)
  })

  it('TIME-28 EC-11 the search folds as the server does and finds a series once', () => {
    expect(fold('STRASSE')).toBe(fold('Straße'))
    const series = (start: string) => ({ ...at(1, 'Training', start, start.replace(/\d\d:\d\d$/, '20:00'), { recurring: true }), id: 50, occurrence: start })
    const snapshot = { occurrences: [series('2026-11-19 18:30'), series('2026-11-26 18:30'), series('2026-12-03 18:30'), { ...at(4, '', '2026-11-27 10:00', '2026-11-27 11:00', { busy_only: true }), notes: 'Training' }] } as unknown as Snapshot
    const found = keptSearch(snapshot, 'training', null, NOW.getTime(), '2026-11-26')
    expect(found.items).toHaveLength(1)
    expect(found.items[0].coming).toBe(true)
    expect(found.items[0].start).toBe(series('2026-11-26 18:30').start)
  })
})

describe('TIME-28 what is kept', () => {
  it('TIME-28 EC-1 signing out and another account forget what was kept; nothing of the one before shows', async () => {
    await open('/')
    expect((shelf.value as Snapshot).account).toBe(1)
    await act(async () => void followAccount(2))
    await settle()
    expect(shelf.value).toBeUndefined()
    expect(await readKept(2)).toBeNull()
    expect(await readKept()).toBeNull()
    // A fresh start without a connection: the account of before is never taken for someone else.
    shelf.value = { ...(JSON.parse(JSON.stringify({ account: 1, takenAt: NOW.getTime(), start: 'x', end: 'y', me: robin, calendars: { calendars: [] }, collections: [], occurrences: [] }))) }
    forgetAccount()
    await act(async () => void followAccount(3))
    await settle()
    expect(await readKept()).toBeNull()
  })

  it('TIME-28 EC-1 signing out forgets what was kept on this device', async () => {
    await open('/')
    expect(shelf.value).toBeDefined()
    // Signing out (`state/auth.tsx` signOut) ends with no account in the tab.
    await act(async () => void followAccount(null))
    await settle()
    expect(shelf.value).toBeUndefined()
    expect(shelf.cleared).toBeGreaterThan(0)
  })

  it('TIME-28 EC-1 a page that finds nobody signed in forgets what was kept', async () => {
    shelf.value = { account: 1, takenAt: NOW.getTime(), start: 'x', end: 'y', me: robin, calendars: { calendars: [] }, collections: [], occurrences: [] }
    views(fake({ more: (_method, path) => (path === '/api/setup' ? ok({ needs_setup: false, code_required: true, signed_in: false, version: '0.1.0', min_password: 12 }) : undefined) }))
    await render(
      <AuthProvider>
        <App />
      </AuthProvider>,
      '/',
    )
    await settle()
    expect(shelf.value).toBeUndefined()
  })

  it('TIME-28 EC-2 too little room: nextime works on, without reading offline, and says so once', async () => {
    shelf.fail = true
    await open('/')
    expect(box.textContent).toContain('Zahnarzt Ben')
    expect(text('offline-full')).toContain('Auf diesem Gerät ist zu wenig Platz frei')
    await click(one('offline-full')!.querySelector('button'))
    expect(one('offline-full')).toBeNull()
    await goOffline()
    expect(text('offline-state')).toBe('Ohne Verbindung · nur lesen')
  })

  it('TIME-28 AC-3 outside the installed app nothing is kept: a browser tab only says it is without a connection', async () => {
    setPwaState({ standalone: false })
    await open('/')
    expect(calls.some((call) => call.path.startsWith('/api/offline'))).toBe(false)
    expect(shelf.value).toBeUndefined()
    await goOffline()
    expect(text('offline-state')).toBe('Ohne Verbindung · nur lesen')
  })

  it('TIME-28 EC-6 started without a connection it shows what was kept; a session that ran out asks for signing in, the events stay readable', async () => {
    await open('/')
    const kept = shelf.value
    stop()
    start()
    const served = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (down) throw new TypeError('Failed to fetch')
      return served(input, init)
    }))
    forgetAccount()
    resetConnection()
    setShelf(shelf)
    shelf.value = kept
    down = true
    // The session ran out on the server meanwhile.
    views(fake({ more: (_method, path) => (path === '/api/auth/me' || path.startsWith('/api/events') || path === '/api/setup' ? { status: 401, body: { detail: { code: 'sign_in_required' } } } : undefined) }))
    await render(
      <AuthProvider>
        <App />
      </AuthProvider>,
      '/',
    )
    await settle()
    expect(one('view-today')).not.toBeNull()
    expect(box.textContent).toContain('Zahnarzt Ben')
    expect(text('offline-state')).toBe('Ohne Verbindung · Stand von heute 16:05 · nur lesen')
    await comeBack()
    await wait(5)
    await settle()
    expect(one('session-expired')).not.toBeNull()
    expect(box.textContent).toContain('Zahnarzt Ben')
    expect(shelf.value).toBeDefined()
  })

  it('TIME-28 EC-7 a new version waiting offers itself as a bar with "Neu laden"', async () => {
    await open('/')
    expect(one('pwa-update')).toBeNull()
    act(() => setPwaState({ update: true }))
    expect(text('pwa-update')).toContain('Es gibt eine neue Version von nextime.')
    expect(byText('Neu laden', one('pwa-update')!)).toBeDefined()
  })

  it('TIME-28 EC-9 back from the background after midnight it shows the new day and fetches anew', async () => {
    await open('/')
    expect(text('today-date')).toContain('Donnerstag')
    const before = calls.length
    vi.setSystemTime(new Date('2026-11-26T23:30:00Z'))
    await act(async () => void document.dispatchEvent(new Event('visibilitychange')))
    await settle()
    expect(text('today-date')).toContain('Freitag')
    expect(calls.slice(before).some((call) => call.path.startsWith('/api/events?'))).toBe(true)
  })

  it('TIME-28 AC-3 what is kept is what the server gave, for the account, a week back and four weeks ahead', async () => {
    await open('/')
    const kept = shelf.value as Snapshot
    const busy = kept.occurrences.find((row) => row.busy_only)!
    expect([busy.title, busy.location, busy.notes]).toEqual(['', '', ''])
    expect(kept.account).toBe(1)
    expect(kept.me.display).toEqual(BERLIN)
    expect(kept.calendars.calendars.map((entry) => entry.name)).toContain('Familie')
    const asked = new URL(calls.find((call) => call.path.startsWith('/api/offline?'))!.path, 'http://x').searchParams
    // From Thursday 19 November 00:00 to Friday 25 December 00:00 in Berlin.
    expect([asked.get('start'), asked.get('end')]).toEqual(['2026-11-18T23:00:00.000Z', '2026-12-24T23:00:00.000Z'])
  })
})

describe('TIME-28 after the review', () => {
  it('TIME-28 EC-4 a 503 of one view while the health question is answered is no reason to go offline', async () => {
    let failing = false
    const server = fake({
      more: (_method, path) => (failing && path.startsWith('/api/events?') ? { status: 503, body: { detail: { code: 'internal_error' } } } : undefined),
    })
    await open('/week', server)
    const before = calls.length
    failing = true
    setViewDay('2026-12-03')
    await settle()
    expect(isOffline()).toBe(false)
    expect(calls.slice(before).filter((call) => call.path === '/api/health')).toHaveLength(1)
  })

  it('TIME-28 AC-4 the health questions stop when the connection is back', async () => {
    await open('/week')
    await goOffline()
    expect(probing()).toBe(true)
    await comeBack()
    expect(isOffline()).toBe(false)
    expect(probing()).toBe(false)
  })

  it('TIME-28 AC-4 a view that moves on stops the question before', async () => {
    const signals: AbortSignal[] = []
    await open('/week')
    const served = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith('/api/events?') && init?.signal) signals.push(init.signal)
      return served(input, init)
    }))
    setViewDay('2026-12-03')
    await settle()
    setViewDay('2026-12-10')
    await settle()
    expect(signals.length).toBeGreaterThanOrEqual(2)
    expect(signals[0].aborted).toBe(true)
    expect(signals[signals.length - 1].aborted).toBe(false)
  })

  it('TIME-28 EC-1 a snapshot written while the account changes is not kept', async () => {
    await open('/')
    const kept = shelf.value as Snapshot
    shelf.put = async (value: Snapshot) => {
      shelf.value = JSON.parse(JSON.stringify(value))
      // Another account signs in while the browser writes.
      followAccount(2)
    }
    expect(await keep({ ...kept, takenAt: kept.takenAt + 1 })).toBe(false)
    expect(shelf.value).toBeUndefined()
  })

  it('TIME-28 AC-3 the panel without a connection never shows notes of "busy only"', async () => {
    await open('/')
    const kept = shelf.value as Snapshot
    const busy = kept.occurrences.find((row) => row.busy_only)!
    // Even if something put notes there, the panel does not show them for a block.
    shelf.value = { ...kept, occurrences: kept.occurrences.map((row) => (row.busy_only ? { ...row, notes: 'Geheim', title: 'Geheim' } : row)) }
    setShelf(shelf)
    await readKept(1)
    await act(async () => markUnreachable())
    const { OfflinePanel } = await import('./features/offline/OfflinePanel')
    await render(<OfflinePanel target={{ kind: 'open', id: busy.id, occurrence: busy.occurrence }} />)
    expect(text('offline-panel')).toContain('Belegt')
    expect(text('offline-panel')).not.toContain('Geheim')
  })

  it('TIME-28 AC-3 without a connection the calendar menu, showing and hiding, collections and the settings change nothing', async () => {
    await open('/')
    await goOffline()
    const before = calls.filter((call) => call.method !== 'GET').length
    // The eye waits for the connection; the calendar alone in the month is the device's own and works.
    expect((all('calendar-eye')[0] as HTMLButtonElement).disabled).toBe(true)
    await click(one('calendar-list')!.querySelector('[aria-haspopup="menu"]'))
    expect(one('calendar-menu')).not.toBeNull()
    for (const id of ['calendar-edit', 'calendar-share', 'calendar-up', 'calendar-down', 'calendar-delete']) {
      expect((one(id) as HTMLButtonElement).disabled, id).toBe(true)
    }
    expect((one('calendar-only') as HTMLButtonElement).disabled).toBe(false)
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    await settle()
    expect(calls.filter((call) => call.method !== 'GET').length).toBe(before)
  })

  it('TIME-28 AC-3 the collection menu offers no new or changed collection without a connection', async () => {
    await open('/')
    await goOffline()
    await click(one('collection-button'))
    expect((one('collection-new') as HTMLButtonElement).disabled).toBe(true)
  })

  it('TIME-28 AC-3 EC-1 the settings are read only without a connection, signing out stays', async () => {
    await open('/settings/profile')
    await goOffline()
    expect(text('settings-offline')).toBe('Ohne Verbindung sind die Einstellungen nur zu lesen. Ändern geht wieder mit Verbindung.')
    expect((one('settings-fieldset') as HTMLFieldSetElement).disabled).toBe(true)
    const signOut = one('sign-out') as HTMLButtonElement
    expect(signOut.closest('fieldset')).toBeNull()
    expect(signOut.disabled).toBe(false)
  })

  it('TIME-28 AC-3 EC-5 a panel open when the connection goes keeps what was typed and greys out saving', async () => {
    await open('/week')
    await click(one('new-event'))
    type(one('field-title') as HTMLInputElement, 'Elternabend')
    await act(async () => markUnreachable())
    await settle()
    expect((one('panel-save') as HTMLButtonElement).disabled).toBe(true)
    expect(text('panel-offline')).toBe('Ohne Verbindung nur lesen. Ändern geht wieder mit Verbindung.')
    expect((one('field-title') as HTMLInputElement).value).toBe('Elternabend')
    await act(async () => markReachable())
    await settle()
    expect((one('panel-save') as HTMLButtonElement).disabled).toBe(false)
  })

  it('TIME-28 EC-1 signing out without a connection on a shared device: the next start with a connection signs out first', async () => {
    await open('/settings/profile')
    await goOffline()
    await click(one('sign-out'))
    expect(localStorage.getItem(PENDING_SIGN_OUT)).toBe('1')
    expect(shelf.value).toBeUndefined()
    expect(box.textContent).not.toContain('Zahnarzt Ben')
    // The next start, still without a connection: signed out, nothing of before.
    forgetAccount()
    await render(
      <AuthProvider>
        <App />
      </AuthProvider>,
      '/',
    )
    await settle()
    expect(one('view-today')).toBeNull()
    // With a connection: the sign-out goes first, before anything else.
    down = false
    calls.length = 0
    forgetAccount()
    await render(
      <AuthProvider>
        <App />
      </AuthProvider>,
      '/',
    )
    await settle()
    expect(calls[0].method).toBe('POST')
    expect(calls[0].path).toBe('/api/auth/logout')
    expect(localStorage.getItem(PENDING_SIGN_OUT)).toBeNull()
  })

  it('TIME-28 EC-10 "Fertig" on the search page shows wherever the bar at the bottom shows', async () => {
    await open('/search')
    expect(one('search-done')!.className).toContain('lg:hidden')
    expect(one('search-done')!.className).not.toContain('md:hidden')
  })
})

