/** TIME-14 calm display in the interface: background entries small, the person at events of shared calendars,
 * an event shown once from two calendars and the question in which to change or delete it, "Belegt" hatched,
 * the switch for duplicates, and differences that need no color. */
import { act } from 'react'

import App from './App'
import { eventDetail } from './calendarFakes'
import type { Occurrence } from './features/calendars/api'
import { setViewDay } from './features/views/state'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, ok, render, settle, start, stop, type } from './testing'
import { anna, at, ben, robin, NOW, theWeek, views, type Fake } from './viewFakes'

/** The style sheet, read as a file (`node:fs` through a name, as `i18n/used.test.ts` does it). */
const fsName = 'node:fs'
const { readFileSync } = (await import(/* @vite-ignore */ fsName)) as { readFileSync: (file: string, encoding: string) => string }

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  localStorage.clear()
  setViewDay(null)
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

async function open(where: string, fake: Fake): Promise<void> {
  views(fake)
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
}

const all = (id: string, scope: ParentNode = document) => [...scope.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const text = (id: string) => one(id)?.textContent ?? ''

/** A tap on an event in the grid: pointer down and up without moving (a plain click does not drag). */
async function tap(element: Element): Promise<void> {
  for (const type of ['pointerdown', 'pointerup']) await act(async () => void element.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: 394, clientY: 700, button: 0 })))
  await settle()
}

/** "Elternabend" in "Persönlich" (2) and in "Familie" (1), shown once. */
function twice(writable: [boolean, boolean] = [true, true]): Occurrence {
  return at(2, 'Elternabend', '2026-11-26 19:30', '2026-11-26 21:00', {
    id: 50,
    writable: writable[0],
    sources: [
      { id: 50, occurrence: null, calendar_id: 2, writable: writable[0], etag: '"p"' },
      { id: 51, occurrence: null, calendar_id: 1, writable: writable[1], etag: '"f"' },
    ],
  })
}

function serverOf(rows: Occurrence[]): Fake {
  return {
    rows,
    more: (method, path) => {
      if (method === 'GET' && path.startsWith('/api/events/50')) return ok(eventDetail({ id: 50, calendar_id: 2, title: 'Elternabend', etag: '"p"', writable: rows[0].writable !== false }))
      if (method === 'GET' && path.startsWith('/api/events/51')) return ok(eventDetail({ id: 51, calendar_id: 1, title: 'Elternabend', etag: '"f"' }))
      if (method === 'PATCH' || method === 'DELETE') return { status: 200, body: eventDetail({ id: 51 }) }
      return undefined
    },
  }
}

describe('TIME-14 background', () => {
  it('TIME-14 AC-1 EC-10 background entries stand small in one line with "+n", a long one on each of its days', async () => {
    const rows = [
      at(6, 'Gelbe Tonne', '2026-11-26', '2026-11-26'),
      at(5, 'Thanksgiving', '2026-11-26', '2026-11-26'),
      at(5, 'Geburtstag Chris', '2026-11-26', '2026-11-26'),
      at(5, 'Herbstferien', '2026-11-25', '2026-11-28'),
    ]
    await open('/', { rows })
    const line = one('background-today')!
    expect(line.className).toContain('nt-background')
    expect([...line.querySelectorAll('.nt-background-entry')].map((entry) => entry.textContent)).toEqual(['Gelbe Tonne', 'Thanksgiving', 'Geburtstag Chris'])
    expect(text('background-more')).toBe('+1')
    await click(one('background-more'))
    expect(line.querySelectorAll('.nt-background-entry')).toHaveLength(4)
    // Not one of them is an event of today.
    expect(one('nothing-more-title')!.textContent).toBe('Heute steht nichts an.')
    await open('/week', { rows })
    const cells = all('all-day-cell')
    expect([2, 4, 5].map((index) => cells[index].textContent)).toEqual(['Herbstferien', 'Herbstferien', 'Herbstferien'])
    expect(cells[3].textContent).toBe('Gelbe Tonne+3')
    expect(cells[1].textContent).toBe('')
    expect(all('all-day-bar')).toHaveLength(0)
    await open('/month', { rows })
    const day = all('month-day').find((cell) => cell.dataset.date === '2026-11-27')!
    expect(day.querySelector('[data-testid="month-background"]')!.textContent).toBe('Herbstferien')
    expect(all('month-event', day)).toHaveLength(0)
  })

  it('TIME-17 a public holiday stands out in month and week, other background entries do not', async () => {
    const rows = [at(5, 'Erster Weihnachtstag', '2026-11-25', '2026-11-25'), at(6, 'Gelbe Tonne', '2026-11-26', '2026-11-26')]
    await open('/month', { rows })
    const cell = (date: string) => all('month-day').find((each) => each.dataset.date === date)!
    expect(cell('2026-11-25').className).toContain('nt-holiday')
    expect(cell('2026-11-25').querySelector('[data-testid="month-background"]')!.className).toContain('nt-holiday-name')
    expect(cell('2026-11-26').className).not.toContain('nt-holiday')
    expect(cell('2026-11-26').querySelector('[data-testid="month-background"]')!.className).not.toContain('nt-holiday-name')
    await open('/week', { rows })
    const cells = all('all-day-cell')
    expect(cells.filter((each) => each.className.includes('nt-holiday')).map((each) => each.dataset.allDay)).toEqual(['2026-11-25'])
    expect(all('grid-column').filter((each) => each.className.includes('nt-holiday')).map((each) => each.dataset.date)).toEqual(['2026-11-25'])
  })
})

describe('TIME-14 the person at an event', () => {
  it('TIME-14 AC-2 EC-7 EC-8 the uploaded picture, else the initial; an event nobody else entered has none', async () => {
    const rows = [
      at(1, 'Fußball Ben', '2026-11-26 17:00', '2026-11-26 18:00', { person: ben }),
      at(1, 'Zahnarzt Ben', '2026-11-26 18:00', '2026-11-26 19:00', { person: anna }),
      at(2, 'Werkstatt', '2026-11-26 19:00', '2026-11-26 20:00'),
    ]
    await open('/week', { rows })
    const events = all('grid-event')
    expect(events[0].querySelector('[data-testid="avatar-picture"]')!.getAttribute('src')).toBe(`/api/avatars/3?v=${encodeURIComponent(ben.avatar!)}`)
    expect(events[1].querySelector('[data-testid="avatar-initial"]')!.textContent).toBe('A')
    expect(events[2].querySelector('.nt-ev-person')).toBeNull()
    // A picture that does not load falls back to the initial.
    const picture = events[0].querySelector('img')!
    await act(async () => void picture.dispatchEvent(new Event('error')))
    expect(all('grid-event')[0].querySelector('[data-testid="avatar-initial"]')!.textContent).toBe('B')
  })

  it('TIME-14 EC-9 two with the same initial: the panel names the person in full', async () => {
    const andreas = { id: 4, name: 'Andreas', color: 3, avatar: null }
    await open('/week', {
      rows: [at(1, 'Einkaufen', '2026-11-26 10:00', '2026-11-26 11:00', { person: andreas })],
      more: (method, path) => (method === 'GET' && path.startsWith('/api/events/') && !path.startsWith('/api/events?') ? ok(eventDetail({ shared: true, created_by: andreas, updated_by: andreas })) : undefined),
    })
    await tap(one('grid-event')!)
    expect(text('entered-by')).toContain('Andreas')
  })

  it('TIME-14 EC-11 a name and a title with code stay text', async () => {
    const evil = { id: 9, name: '<img src=x onerror=alert(1)>', color: 1, avatar: null }
    await open('/', { rows: [at(1, '<b>Fest</b>', '2026-11-26 18:00', '2026-11-26 19:00', { person: evil })] })
    expect(box.querySelector('img[src="x"], b')).toBeNull()
    expect(text('next-person')).toContain('<img src=x onerror=alert(1)>')
  })
})

describe('TIME-14 one event in two calendars', () => {
  it('TIME-14 AC-3 it stands once with "in 2 Kalendern"; opened, the head names every calendar', async () => {
    await open('/week', serverOf([twice()]))
    expect(all('grid-event')).toHaveLength(1)
    expect(text('in-calendars')).toBe('in 2 Kalendern')
    await tap(one('grid-event')!)
    expect(text('panel-head')).toBe('Persönlich · auch in Familie')
  })

  it('TIME-14 EC-2 changing asks in which calendar, and only that one changes', async () => {
    await open('/week', serverOf([twice()]))
    await tap(one('grid-event')!)
    type(one('field-title') as HTMLInputElement, 'Elternabend Klasse 2')
    await click(one('panel-save'))
    expect(document.querySelector('[role="dialog"]:not([data-event-dialog])')!.getAttribute('aria-label')).toBe('In welchem Kalender ändern?')
    expect(text('source-question')).toContain('„Elternabend“ steht in 2 Kalendern.')
    expect(all('source-choice').map((choice) => choice.textContent)).toEqual(['Persönlich', 'Familie'])
    await click(all('source-choice')[1])
    const changes = calls.filter((call) => call.method === 'PATCH')
    expect(changes.map((call) => call.path)).toEqual(['/api/events/51'])
    expect(changes[0].body).toMatchObject({ title: 'Elternabend Klasse 2', etag: '"f"' })
  })

  it('TIME-14 EC-2 deleting asks the same', async () => {
    await open('/week', serverOf([twice()]))
    await tap(one('grid-event')!)
    await click(one('panel-delete'))
    expect(document.querySelector('[role="dialog"]:not([data-event-dialog])')!.getAttribute('aria-label')).toBe('In welchem Kalender löschen?')
    await click(all('source-choice')[0])
    expect(calls.filter((call) => call.method === 'DELETE').map((call) => call.path)).toEqual(['/api/events/50?scope=all'])
  })

  it('TIME-14 EC-3 a source one may only read is not offered; a drag asks too', async () => {
    await open('/week', serverOf([twice([false, true])]))
    await tap(one('grid-event')!)
    await click(one('panel-close'))
    const block = one('grid-event')!
    await act(async () => void block.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 394, clientY: 700, button: 0 })))
    await act(async () => void block.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 394, clientY: 736, button: 0 })))
    await act(async () => void block.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 394, clientY: 736, button: 0 })))
    await settle()
    expect(all('source-choice').map((choice) => choice.textContent)).toEqual(['Familie'])
    await click(all('source-choice')[0])
    expect(calls.filter((call) => call.method === 'PATCH').map((call) => [call.path, (call.body as { start: string }).start])).toEqual([['/api/events/51', '2026-11-26T20:30']])
  })

  it('TIME-14 AC-4 merging can be switched off in the settings; on from the start', async () => {
    await open('/settings/appearance', { rows: [], more: (method, path) => (method === 'PUT' && path === '/api/me/display' ? ok(robin) : undefined) })
    const options = [...one('display-fields')!.querySelectorAll<HTMLElement>('[data-testid="display-duplicates"] [role="radio"]')]
    expect(options.map((option) => option.getAttribute('aria-checked'))).toEqual(['true', 'false'])
    await click(options[1])
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/me/display')!.body).toEqual({ merge_duplicates: false })
  })
})

describe('TIME-14 busy only and the differences without color', () => {
  it('TIME-14 AC-5 a calendar shared as busy only shows hatched "Belegt" with the person, no title', async () => {
    await open('/week', { rows: theWeek() })
    const busy = all('grid-event').find((event) => event.className.includes('nt-busy'))!
    expect(busy.querySelector('.nt-ev-title')!.textContent).toBe('Belegt')
    expect(busy.textContent).toContain('Anna')
    expect(busy.querySelector('[data-testid="avatar-initial"]')).not.toBeNull()
  })

  it('TIME-14 AC-6 background without a block, "Belegt" hatched, an invitation dashed: told apart without color', async () => {
    const css = readFileSync('src/styles/index.css', 'utf-8')
    const rule = (name: string) => css.slice(css.indexOf(`${name} {`) + name.length + 2, css.indexOf('}', css.indexOf(`${name} {`)))
    expect(rule('.nt-busy')).toContain('repeating-linear-gradient(135deg')
    expect(rule('.nt-invitation')).toContain('dashed')
    expect(rule('.nt-background')).not.toContain('background')
    await open('/week', { rows: theWeek() })
    expect(one('all-day-cell')!.closest('[data-testid="all-day-row"]')!.querySelector('.nt-background .nt-bar')).toBeNull()
    expect(all('grid-event').filter((event) => event.className.includes('nt-busy'))).toHaveLength(1)
  })
})
