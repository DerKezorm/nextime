/**
 * The web interface as nexnotes has it, so that one sees at once that both apps come from the same house: the sidebar
 * as wide as set on this device, its heads with "+", the "…" of a row, the menus, and the notice at the bottom with
 * "Rückgängig" after deleting.
 */
import { act } from 'react'

import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import i18n from './i18n'
import { COLUMNS } from './lib/columns'
import { forgetPrefs, prefKey } from './lib/devicePref'
import { hideNotice, NOTICE_MS, showNotice, UNDO_MS } from './lib/notice'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, ok, render, serve, signedIn, start, stop } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })

beforeEach(async () => {
  localStorage.clear()
  forgetPrefs()
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  act(() => hideNotice())
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

type Handler = (method: string, path: string, body: unknown) => { status: number; body: unknown } | undefined

function server(more: Handler = () => undefined, lists = calendarList([calendar(1, 'Persönlich', { default: true }), calendar(2, 'Verein', { events: 3 })])) {
  serve(signedIn(anna, (method, path, body) => more(method, path, body) ?? (path.startsWith('/api/calendars') ? ok(lists) : undefined)))
}

async function open(at = '/'): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const handle = () => one('column-handle-sidebar')!
const sidebarWidth = () => box.querySelector<HTMLElement>('nav[aria-label]')!.style.width
const press = (key: string) => act(() => void handle().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })))

describe('the sidebar as in nexnotes', () => {
  it('arrow keys, Home and End and a double click change its width, within its bounds', async () => {
    server()
    await open()
    expect(sidebarWidth()).toBe('260px')
    expect(handle().getAttribute('role')).toBe('separator')
    expect(handle().getAttribute('aria-label')).toBe('Breite der Seitenleiste')
    press('ArrowRight')
    expect(sidebarWidth()).toBe('276px')
    expect(handle().getAttribute('aria-valuenow')).toBe('276')
    press('End')
    expect(sidebarWidth()).toBe(`${COLUMNS.sidebar.max}px`)
    press('Home')
    expect(sidebarWidth()).toBe(`${COLUMNS.sidebar.min}px`)
    act(() => void handle().dispatchEvent(new MouseEvent('dblclick', { bubbles: true })))
    expect(sidebarWidth()).toBe('260px')
  })

  it('dragging the edge sets the width, which this device keeps for this account', async () => {
    server()
    await open()
    await act(async () => handle().dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, clientX: 260, button: 0 })))
    await act(async () => handle().dispatchEvent(new MouseEvent('pointermove', { clientX: 330 })))
    await act(async () => handle().dispatchEvent(new MouseEvent('pointerup', {})))
    expect(sidebarWidth()).toBe('330px')
    expect(localStorage.getItem(prefKey('column.sidebar', 1))).toBe('330')
    forgetPrefs()
    await open()
    expect(sidebarWidth()).toBe('330px')
  })

  it('the drawer on a phone has no handle and keeps 260 px', async () => {
    server()
    localStorage.setItem(prefKey('column.sidebar', 1), '400')
    await open()
    await click(one('profile-button'))
    const navs = box.querySelectorAll<HTMLElement>('nav[aria-label="' + i18n.t('app.mainMenu') + '"]')
    expect(navs).toHaveLength(2)
    expect(box.querySelectorAll('[data-testid="column-handle-sidebar"]')).toHaveLength(1)
    expect(navs[1].style.width).toBe('260px')
  })

  it('"+" stands in the head of the calendars, the trash under a line at the end of the places', async () => {
    server()
    await open()
    const add = one('add-calendar')!
    expect(add.closest('div')!.querySelector('h3')?.textContent).toBe('Eigene')
    expect(add.getAttribute('aria-label')).toBe('Kalender hinzufügen')
    const places = one('sidebar-places')!
    expect(places.contains(one('trash-entry'))).toBe(true)
    expect(one('trash-entry')!.parentElement!.className).toContain('border-t')
    await click(add)
    expect(one('add-calendar-dialog')).not.toBeNull()
  })

  it('without a calendar the head "Kalender" carries the "+"', async () => {
    server(undefined, calendarList([]))
    await open()
    expect(one('add-calendar')!.parentElement!.textContent).toContain('Kalender')
    expect(one('calendar-list')!.textContent).toContain('Noch keine Kalender.')
  })

  it('a right click opens the menu of a calendar; delete is red in it', async () => {
    server()
    await open()
    const row = box.querySelectorAll('[data-testid="calendar-toggle"]')[1].closest('li')!
    await act(async () => void row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 200 })))
    const menu = one('calendar-menu')!
    expect(menu).not.toBeNull()
    const remove = byText('Löschen …', menu)!
    expect(remove.className).toContain('text-bad-500')
    expect(remove.className).toContain('hover:text-bad-500')
  })
})

describe('notices as in nexnotes', () => {
  it('one at a time; gone after 5 s, with "Rückgängig" after 10 s', async () => {
    vi.useFakeTimers()
    server()
    await open()
    act(() => showNotice({ text: 'Eins' }))
    act(() => showNotice({ text: 'Zwei' }))
    expect(document.querySelectorAll('[data-testid="notice-bar"]')).toHaveLength(1)
    expect(one('toast')!.textContent).toBe('Zwei')
    expect(one('notice-bar')!.querySelector('button')).toBeNull()
    act(() => void vi.advanceTimersByTime(NOTICE_MS - 1))
    expect(one('notice-bar')).not.toBeNull()
    act(() => void vi.advanceTimersByTime(1))
    expect(one('notice-bar')).toBeNull()
    act(() => showNotice({ text: 'Drei', undo: async () => undefined }))
    act(() => void vi.advanceTimersByTime(NOTICE_MS))
    expect(one('notice-undo')).not.toBeNull()
    act(() => void vi.advanceTimersByTime(UNDO_MS - NOTICE_MS))
    expect(one('notice-bar')).toBeNull()
  })

  it('a deleted calendar comes back with "Rückgängig"', async () => {
    server((method, path) => (method === 'DELETE' && path === '/api/calendars/2' ? ok(calendarList([calendar(1, 'Persönlich', { default: true })])) : path === '/api/trash/calendars/2/restore' ? ok({ id: 2 }) : undefined))
    await open()
    await click(box.querySelectorAll('[data-testid="calendar-list"] [aria-haspopup="menu"]')[1])
    await click(byText('Löschen …'))
    await click(byText('Löschen', one('delete-calendar-dialog')!))
    expect(one('toast')!.textContent).toBe('Kalender „Verein“ liegt im Papierkorb.')
    await click(one('notice-undo'))
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/trash/calendars/2/restore')).toBe(true)
    expect(one('toast')!.textContent).toBe('„Verein“ ist wieder da.')
  })

  async function deleteEvent(recurring: boolean, trash: { kind: 'event' | 'part'; id: number }, scope?: string): Promise<void> {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-11-26T15:05:00Z'))
    const detail = eventDetail({ title: 'Elternabend', recurring, ...(recurring ? { occurrence: '20261126T173000Z' } : {}) })
    server((method, path) => {
      if (path.startsWith('/api/events?'))
        return ok([{ id: detail.id, occurrence: detail.occurrence, calendar_id: detail.calendar_id, title: detail.title, busy: true, busy_only: false, recurring, all_day: false, start: '2026-11-26T17:30:00Z', end: '2026-11-26T19:00:00Z', time_zone: 'Europe/Berlin' }])
      if (path.startsWith(`/api/events/${detail.id}`) && method === 'GET') return ok(detail)
      if (path.startsWith(`/api/events/${detail.id}`) && method === 'DELETE') return ok({ trash })
      if (path === `/api/trash/${trash.kind}s/${trash.id}/restore`) return ok({ id: detail.id, calendar_id: detail.calendar_id })
      return undefined
    })
    await open()
    await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
    await click(one('panel-delete'))
    if (scope) await click(byText(scope, one('series-question')!))
    expect(one('event-panel')).toBeNull()
    expect(one('toast')!.textContent).toBe('„Elternabend“ liegt im Papierkorb.')
    await click(one('notice-undo'))
    expect(calls.some((call) => call.method === 'POST' && call.path === `/api/trash/${trash.kind}s/${trash.id}/restore`)).toBe(true)
    expect(one('toast')!.textContent).toBe('„Elternabend“ ist wieder da.')
  }

  it('a deleted event says it is in the trash and comes back with "Rückgängig"', async () => {
    await deleteEvent(false, { kind: 'event', id: eventDetail().id })
  })

  it('TIME-23 AC-4 one occurrence or the following come back with "Rückgängig", from the entry the server names', async () => {
    await deleteEvent(true, { kind: 'part', id: 77 }, 'Nur diesen Termin')
    act(() => hideNotice())
    calls.length = 0
    await deleteEvent(true, { kind: 'part', id: 78 }, 'Diesen und alle folgenden')
  })
})
