/** TIME-4 in the interface: the list of calendars by source, "New calendar", "Add calendar", hiding, the order,
 * deleting, names as text, the colors by keyboard, and where new events land when the default moved. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import type { CalendarList } from './features/calendars/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, ok, render, serve, signedIn, start, stop, type } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: 'auto', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.restoreAllMocks()
})

function server(list: CalendarList, more: (method: string, path: string, body: unknown) => { status: number; body: unknown } | undefined = () => undefined) {
  serve(signedIn(anna, (method, path, body) => more(method, path, body) ?? (path.startsWith('/api/calendars') ? ok(list) : undefined)))
}

async function open(): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    '/',
  )
}

const list = () => box.querySelector('[data-testid="calendar-list"]')!

describe('TIME-4 the calendar list', () => {
  it('TIME-4 AC-9 EC-12 comes by group, and a source without calendars has no heading', async () => {
    server(calendarList([calendar(1, 'Persönlich', { default: true }), calendar(2, 'Familie', { color: 'green', shared: true }), calendar(3, 'Annas', { group: 'shared', right: 'busy', owner: 'Anna', writable: false })]))
    await open()
    const headings = [...list().querySelectorAll('h3')].map((node) => node.textContent)
    expect(headings).toEqual(['Eigene', 'Mit mir geteilt'])
    expect(list().textContent).not.toContain('iCloud')
    const rows = [...list().querySelectorAll('[data-testid="calendar-toggle"]')].map((node) => node.textContent)
    expect(rows).toEqual(['Persönlich', 'Familiegeteilt', 'Annasbelegt'])
    expect(list().querySelector('[data-color="green"]')).not.toBeNull()
  })

  it('TIME-4 AC-3 TIME-4 eye hides a calendar and shows it again; a hidden one shows the crossed eye; the eye opens nothing', async () => {
    server(calendarList([calendar(1, 'Persönlich'), calendar(2, 'Verein', { hidden: true })]))
    await open()
    const eyes = list().querySelectorAll<HTMLButtonElement>('[data-testid="calendar-eye"]')
    expect([...eyes].map((eye) => eye.getAttribute('aria-label'))).toEqual(['Persönlich ausblenden', 'Verein einblenden'])
    expect(eyes[1].getAttribute('aria-pressed')).toBe('false')
    expect(eyes[1].querySelector('svg')?.getAttribute('class')).toContain('eye-off')
    // A hidden calendar's eye stands there always; a shown one's only on hovering.
    expect(eyes[1].className).toMatch(/(^| )flex( |$)/)
    expect(eyes[0].className).toContain('hidden')
    await click(eyes[0])
    expect(calls.find((call) => call.method === 'PATCH')).toMatchObject({ path: '/api/calendars/1', body: { hidden: true } })
    expect(box.querySelector('[data-testid="only-bar"]')).toBeNull()
    expect(box.querySelector('[data-testid="view-month"]')).toBeNull()
    await click(eyes[1])
    expect(calls.filter((call) => call.method === 'PATCH').at(-1)).toMatchObject({ path: '/api/calendars/2', body: { hidden: false } })
  })

  it('TIME-4 AC-5 the menu moves a calendar within its group', async () => {
    server(calendarList([calendar(1, 'A'), calendar(2, 'B'), calendar(3, 'C'), calendar(4, 'Geteilt', { group: 'shared', right: 'read', writable: false })]))
    await open()
    await click(list().querySelectorAll('[aria-haspopup="menu"]')[0])
    await click(byText('Nach unten'))
    expect(calls.find((call) => call.method === 'PUT')).toMatchObject({ path: '/api/calendars/order', body: { ids: [2, 1, 3] } })
    await click(list().querySelectorAll('[aria-haspopup="menu"]')[3])
    expect(byText('Bearbeiten …')).toBeUndefined()
    expect(byText('Löschen …')).toBeUndefined()
  })

  it('TIME-4 AC-4 deleting asks first and then puts the calendar into the trash', async () => {
    server(calendarList([calendar(1, 'Persönlich'), calendar(2, 'Verein', { events: 12 })]))
    await open()
    await click(list().querySelectorAll('[aria-haspopup="menu"]')[1])
    await click(byText('Löschen …'))
    const question = document.querySelector('[data-testid="delete-calendar-dialog"]')!
    expect(document.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe('Kalender „Verein“ löschen?')
    expect(question.textContent).toContain('Er kommt mit seinen 12 Terminen in den Papierkorb.')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(byText('Löschen', question))
    expect(calls.find((call) => call.method === 'DELETE')?.path).toBe('/api/calendars/2')
  })

  it('TIME-4 EC-4 EC-5 a name with code stays text, a long one is shortened and whole in the tooltip', async () => {
    const long = 'Ein sehr langer Kalendername mit genau sechzig Zeichen ......'
    server(calendarList([calendar(1, '<img src=x onerror=alert(1)>'), calendar(2, long)]))
    await open()
    expect(list().querySelector('img')).toBeNull()
    expect(list().textContent).toContain('<img src=x onerror=alert(1)>')
    const name = list().querySelectorAll('[data-testid="calendar-name"]')[1]
    expect(name.className).toContain('truncate')
    expect(name.closest('button')?.getAttribute('title')).toContain(long)
  })
})

describe('TIME-4 new calendar', () => {
  async function openDialog(): Promise<HTMLElement> {
    await click(box.querySelector('[data-testid="add-calendar"]'))
    await click(document.querySelector('[data-testid="way-own"]'))
    return document.querySelector<HTMLElement>('[data-testid="calendar-dialog"]')!
  }

  it('TIME-4 AC-8 AC-6 "Kalender hinzufügen" shows the seven ways and, up to 20 calendars, no count', async () => {
    server(calendarList(Array.from({ length: 9 }, (_, index) => calendar(index + 1, `K${index}`))))
    await open()
    await click(box.querySelector('[data-testid="add-calendar"]'))
    const ways = document.querySelectorAll('[data-testid^="way-"]')
    expect(ways).toHaveLength(7)
    expect(document.querySelector('[data-testid="way-own"]')?.textContent).toContain('lesen und schreiben')
    expect(document.querySelector('[data-testid="calendar-count"]')).toBeNull()
    await click(document.querySelector('[data-testid="way-own"]'))
    expect(document.querySelector('[data-testid="calendar-dialog"]')).not.toBeNull()
  })

  it('TIME-4 AC-6 above 20 calendars nextime says it may get slower', async () => {
    server(calendarList(Array.from({ length: 21 }, (_, index) => calendar(index + 1, `K${index}`))))
    await open()
    await click(box.querySelector('[data-testid="add-calendar"]'))
    expect(document.querySelector('[data-testid="calendar-count"]')?.textContent).toBe('Du hast 21 Kalender; ab 20 kann nextime langsamer werden.')
  })

  it('TIME-4 AC-1 AC-7 the dialog has name, eleven colors, both switches, the reminder and creates at once', async () => {
    let current = calendarList([calendar(1, 'Persönlich')])
    server(current, (method, path) => {
      if (method === 'POST' && path === '/api/calendars') current = calendarList([calendar(1, 'Persönlich'), calendar(2, 'Familie', { color: 'green' })])
      return path === '/api/calendars' ? { status: method === 'POST' ? 201 : 200, body: current } : undefined
    })
    await open()
    const dialog = await openDialog()
    expect(document.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe('Neuer Kalender')
    const colors = [...dialog.querySelectorAll('[role="radio"][data-testid^="color-"]')]
    expect(colors.map((node) => node.getAttribute('aria-label'))).toEqual(['Schiefer', 'Rot', 'Orange', 'Gelb', 'Grün', 'Türkis', 'Blau', 'Violett', 'Pink', 'Braun', 'Grau'])
    expect(colors[0].getAttribute('aria-checked')).toBe('true')
    // A box, its name and the quiet hint below it (no sliding switch, decided while testing, 2026-11-23).
    const background = [...dialog.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find((box) => box.getAttribute('aria-label') === 'Als Hintergrund zeigen')!
    expect(document.getElementById(background.getAttribute('aria-describedby')!)?.textContent).toBe('klein oben im Tag, etwa Feiertage')
    expect(dialog.querySelector('.nt-switch')).toBeNull()
    expect(dialog.textContent).toContain('Standard für neue Termine')
    // The reminder for new events stands with the other defaults (TIME-4 defaults): none from the start.
    expect(dialog.querySelector('[data-testid="new-event-defaults"]')).not.toBeNull()
    expect(dialog.querySelectorAll('[data-testid="new-event-defaults"] [data-testid="reminder-chip"]')).toHaveLength(0)
    expect(dialog.textContent).toContain('Liegt auf deinem nextime-Server. Teilen und aufs iPhone bringen kannst du ihn danach.')
    expect(byText('Abbrechen', dialog)).toBeDefined()
    type(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!, '  Familie  ')
    await click(dialog.querySelector('[data-testid="color-green"]'))
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    const sent = calls.find((call) => call.method === 'POST' && call.path === '/api/calendars')
    expect(sent?.body).toMatchObject({ name: 'Familie', color: 'green', background: false, default: false, reminder: 0 })
    expect(String((sent?.body as { client_ref: string }).client_ref)).toMatch(/^ref-/)
    expect(document.querySelector('[data-testid="calendar-dialog"]')).toBeNull()
    expect(list().textContent).toContain('Familie')
  })

  it('TIME-4 EC-3 an empty, blank or too long name says so at the field and sends nothing', async () => {
    server(calendarList([calendar(1, 'Persönlich')]))
    await open()
    const dialog = await openDialog()
    const field = dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!
    type(field, '   ')
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(dialog.textContent).toContain('Bitte einen Namen eingeben.')
    type(field, 'x'.repeat(61))
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(dialog.textContent).toContain('Höchstens 60 Zeichen, jetzt 61.')
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/calendars')).toBe(false)
  })

  it('TIME-4 EC-6 a second click sends once, and a retry after a broken connection keeps the inputs and the same request', async () => {
    server(calendarList([calendar(1, 'Persönlich')]))
    await open()
    const dialog = await openDialog()
    const realFetch = globalThis.fetch
    let posts = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST' && String(input) === '/api/calendars') {
        posts += 1
        calls.push({ method: 'POST', path: String(input), body: JSON.parse(String(init.body)), headers: {} })
        throw new TypeError('Failed to fetch')
      }
      return realFetch(input, init)
    }))
    type(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!, 'Verein')
    const save = dialog.querySelector<HTMLButtonElement>('[data-testid="calendar-save"]')!
    await act(async () => {
      save.click()
      save.click()
    })
    expect(posts).toBe(1)
    expect(dialog.querySelector('[role="alert"]')?.textContent).toBeTruthy()
    expect(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')?.value).toBe('Verein')
    await click(save)
    const refs = calls.filter((call) => call.method === 'POST' && call.path === '/api/calendars').map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs).toHaveLength(2)
    expect(refs[0]).toBe(refs[1])
  })

  it('TIME-4 EC-13 every color is reachable by the arrow keys and has a name', async () => {
    server(calendarList([calendar(1, 'Persönlich')]))
    await open()
    const dialog = await openDialog()
    const group = dialog.querySelector<HTMLElement>('[role="radiogroup"]')!
    const slate = dialog.querySelector<HTMLButtonElement>('[data-testid="color-slate"]')!
    expect(slate.tabIndex).toBe(0)
    slate.focus()
    await act(async () => void group.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })))
    const red = dialog.querySelector<HTMLButtonElement>('[data-testid="color-red"]')!
    expect(red.getAttribute('aria-checked')).toBe('true')
    expect(document.activeElement).toBe(red)
    await act(async () => void group.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })))
    await act(async () => void group.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true })))
    expect(dialog.querySelector('[data-testid="color-gray"]')?.getAttribute('aria-checked')).toBe('true')
  })
})

describe('TIME-4 the default for new events', () => {
  it('TIME-4 EC-1 a new event says where it lands when the default calendar is gone', async () => {
    server(calendarList([calendar(1, 'Persönlich')], { default_id: 1, default_moved: 'deleted', default_was: 'Familie' }))
    await open()
    await click(box.querySelector('[data-testid="new-event"]'))
    expect(document.querySelector('[data-testid="event-panel"]')?.textContent).toContain('„Familie“ gibt es nicht mehr. Neue Termine landen jetzt in „Persönlich“.')
    expect(document.querySelector<HTMLSelectElement>('[data-testid="field-calendar"]')?.value).toBe('1')
  })

  it('TIME-4 EC-9 a calendar one may only read is not offered for new events', async () => {
    server(calendarList([calendar(1, 'Persönlich'), calendar(2, 'Annas', { group: 'shared', right: 'read', writable: false })]))
    await open()
    await click(list().querySelectorAll('[aria-haspopup="menu"]')[1])
    expect(byText('Als Standard für neue Termine')).toBeUndefined()
    await click(box.querySelector('[data-testid="new-event"]'))
    const options = [...document.querySelectorAll<HTMLOptionElement>('[data-testid="field-calendar"] option')].map((option) => option.textContent)
    expect(options).toEqual(['Persönlich'])
  })
})
