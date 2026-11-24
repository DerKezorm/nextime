/** TIME-13 collections in the interface: the menu, the choice per device, the dialog with its checks, deleting,
 * the calendar suggested for new events, and "Nur diesen zeigen" by Alt+click and by the menu "⋯". */
import { act } from 'react'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import type { Collection } from './features/calendars/api'
import i18n from './i18n'
import { forgetPrefs, prefKey, setPref } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, byText, calls, choose, click, render, settle, start, stop, type } from './testing'
import { CALENDARS, NOW, theWeek, views, type Fake } from './viewFakes'

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  localStorage.clear()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

const FAMILIE: Collection = { id: 8, name: 'Familie', calendar_ids: [1, 2, 4, 6], updated_at: '' }
const ARBEIT: Collection = { id: 9, name: 'Arbeit', calendar_ids: [3, 4, 5], updated_at: '' }

async function open(fake: Partial<Fake> = {}, where = '/'): Promise<void> {
  views({ rows: theWeek(), collections: [ARBEIT, FAMILIE], ...fake })
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
const eventCalls = () => calls.filter((call) => call.path.startsWith('/api/events?')).map((call) => new URL(call.path, 'http://x').searchParams)
const toggles = () => all('calendar-toggle', box.querySelector('[data-testid="sidebar-places"]')!)
const toggle = (name: string) => toggles().find((button) => button.textContent?.includes(name))!

async function altClick(element: HTMLElement): Promise<void> {
  await act(async () => void element.dispatchEvent(new MouseEvent('click', { bubbles: true, altKey: true })))
  await settle()
}

describe('TIME-13 switching', () => {
  it('TIME-13 AC-6 AC-1 EC-3 the menu lists "Alles" ticked and the collections, "Neue Sammlung …" and that the choice holds here only', async () => {
    await open()
    expect(text('collection-button')).toBe('SammlungAlles ▾')
    await click(one('collection-button'))
    const choices = all('collection-choice')
    expect(choices.map((choice) => [choice.textContent, choice.getAttribute('aria-checked')])).toEqual([
      ['Alles', 'true'],
      ['Arbeit', 'false'],
      ['Familie', 'false'],
    ])
    expect(one('collection-new')!.textContent).toBe('Neue Sammlung …')
    // "Alles" is no collection to edit or delete.
    expect(one('collection-edit')).toBeNull()
    expect(one('collection-menu')!.textContent).toContain('Die Wahl gilt nur auf diesem Gerät.')
  })

  it('TIME-13 AC-3 a chosen collection shows only its calendars, and the choice stays on this device', async () => {
    await open()
    await click(one('collection-button'))
    await click(all('collection-choice')[2])
    expect(text('collection-button')).toBe('SammlungFamilie ▾')
    expect(eventCalls().at(-1)!.get('calendars')).toBe('1,2,4,6')
    expect(localStorage.getItem(prefKey('collection', 1))).toBe('8')
    expect(box.querySelector('[data-testid="today-column"]')!.textContent).not.toContain('Teamrunde')
    // Opened again on this device: still "Familie".
    forgetPrefs()
    await open()
    expect(text('collection-button')).toBe('SammlungFamilie ▾')
  })

  it('TIME-13 EC-9 EC-2 a choice that is gone (data cleared, collection deleted elsewhere) counts as "Alles"', async () => {
    setPref('collection', 1, '77')
    await open()
    expect(text('collection-button')).toBe('SammlungAlles ▾')
    expect(eventCalls().at(-1)!.get('calendars')).toBeNull()
    localStorage.clear()
    forgetPrefs()
    await open()
    expect(text('collection-button')).toBe('SammlungAlles ▾')
  })

  it('TIME-13 EC-8 EC-14 many collections scroll in the menu, long names stand whole, a name with code is text', async () => {
    const many = Array.from({ length: 15 }, (_, index) => ({ id: 20 + index, name: `Sammlung mit einem recht langen Namen ${index}`, calendar_ids: [], updated_at: '' }))
    many.push({ id: 40, name: '<img src=x onerror=alert(1)>', calendar_ids: [], updated_at: '' })
    await open({ collections: many })
    await click(one('collection-button'))
    expect(one('collection-menu')!.querySelector('.overflow-y-auto')!.children).toHaveLength(17)
    expect(all('collection-choice')[1].querySelector('span')!.className).not.toContain('truncate')
    expect(document.querySelector('img[src="x"]')).toBeNull()
    expect(all('collection-choice').some((choice) => choice.textContent === '<img src=x onerror=alert(1)>')).toBe(true)
  })
})

describe('TIME-13 the dialog', () => {
  async function newOne(): Promise<void> {
    await click(one('collection-button'))
    await click(one('collection-new'))
  }

  it('TIME-13 AC-7 AC-2 name, calendars of every source with their source, "Abbrechen" and "Sichern"', async () => {
    await open()
    await newOne()
    const dialog = one('collection-dialog')!
    const rows = [...dialog.querySelectorAll('label:has([data-testid="collection-calendar"])')].map((row) => row.textContent)
    expect(rows).toEqual([
      'Familieeigener',
      'Persönlicheigener',
      'Arbeiteigener',
      'Anna · Arbeitgeteilt, belegt',
      'FeiertageHintergrund',
      'MüllabfuhrHintergrund',
      'Vereineigener',
    ])
    expect(one('collection-delete')).toBeNull()
    await click(byText('Abbrechen', dialog))
    expect(one('collection-dialog')).toBeNull()
    expect(calls.filter((call) => call.path.startsWith('/api/collections') && call.method !== 'GET')).toEqual([])
  })

  it('TIME-13 AC-2 EC-6 a new collection with calendars, blanks at the edges gone, emoji kept; it is chosen at once', async () => {
    const stored = [ARBEIT, FAMILIE]
    await open({
      collections: stored,
      more: (method, path, body) => {
        if (path !== '/api/collections' || method !== 'POST') return undefined
        stored.push({ id: 10, name: (body as { name: string }).name, calendar_ids: [7], updated_at: '' })
        return { status: 201, body: { collections: stored, created: 10 } }
      },
    })
    await newOne()
    type(one('collection-name') as HTMLInputElement, '  Verein 🎄 Größe  ')
    await click(all('collection-calendar')[6])
    await click(one('collection-save'))
    const sent = calls.find((call) => call.method === 'POST' && call.path === '/api/collections')!.body as Record<string, unknown>
    expect(sent).toMatchObject({ name: 'Verein 🎄 Größe', calendar_ids: [7] })
    expect(text('collection-button')).toBe('SammlungVerein 🎄 Größe ▾')
  })

  it('TIME-13 EC-6 EC-15 an empty name, more than 60 characters, or the name of another collection say so at the field', async () => {
    await open()
    await newOne()
    await click(one('collection-save'))
    expect(text('collection-name-problem')).toBe('Bitte einen Namen eingeben.')
    type(one('collection-name') as HTMLInputElement, 'x'.repeat(61))
    await click(one('collection-save'))
    expect(text('collection-name-problem')).toBe('Höchstens 60 Zeichen, jetzt 61.')
    type(one('collection-name') as HTMLInputElement, 'familie')
    await click(one('collection-save'))
    expect(text('collection-name-problem')).toBe('Es gibt schon eine Sammlung „familie“.')
    expect(calls.filter((call) => call.method === 'POST' && call.path === '/api/collections')).toEqual([])
  })

  it('TIME-13 EC-10 two quick clicks on "Sichern" save once', async () => {
    await open({ more: (method, path) => (path === '/api/collections' && method === 'POST' ? { status: 201, body: { collections: [ARBEIT, FAMILIE], created: 8 } } : undefined) })
    await newOne()
    type(one('collection-name') as HTMLInputElement, 'Garten')
    const save = one('collection-save')!
    await act(async () => {
      save.click()
      save.click()
    })
    await settle()
    expect(calls.filter((call) => call.method === 'POST' && call.path === '/api/collections')).toHaveLength(1)
  })

  it('TIME-13 EC-11 a broken connection keeps the dialog with everything entered and says it was not saved', async () => {
    await open()
    await newOne()
    type(one('collection-name') as HTMLInputElement, 'Garten')
    await click(all('collection-calendar')[0])
    const fake = globalThis.fetch
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => (String(input) === '/api/collections' && init?.method === 'POST' ? Promise.reject(new TypeError('offline')) : fake(input, init)))
    await click(one('collection-save'))
    expect(text('collection-message')).toBe('Nicht gesichert: Die Verbindung brach ab. Deine Eingaben sind noch da.')
    expect((one('collection-name') as HTMLInputElement).value).toBe('Garten')
    expect((all('collection-calendar')[0] as HTMLInputElement).checked).toBe(true)
  })

  it('TIME-13 AC-7 AC-8 editing takes the change only on "Sichern"; deleting asks, says what stays, and goes back to "Alles"', async () => {
    setPref('collection', 1, '8')
    await open({ more: (method, path) => (path === '/api/collections/8' && method === 'DELETE' ? { status: 200, body: { collections: [ARBEIT] } } : undefined) })
    await click(one('collection-button'))
    await click(one('collection-edit'))
    expect(document.querySelector('[role="dialog"]')!.getAttribute('aria-label')).toBe('Sammlung „Familie“')
    await click(one('collection-delete'))
    expect(text('collection-delete-text')).toBe('Die Sammlung ist danach weg. Kalender und Termine bleiben, wie sie sind.')
    await click(one('collection-delete-confirm'))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/collections/8')).toBe(true)
    expect(text('collection-button')).toBe('SammlungAlles ▾')
  })
})

describe('TIME-13 new events in a collection', () => {
  it('TIME-13 AC-4 a new event lands in the default when it is in the collection, else in the first writable one', async () => {
    setPref('collection', 1, '9')
    await open()
    await click(one('new-event'))
    expect((one('field-calendar') as HTMLSelectElement).value).toBe('3')
    await click(one('panel-close'))
    setPref('collection', 1, '8')
    await open()
    await click(one('new-event'))
    expect((one('field-calendar') as HTMLSelectElement).value).toBe('1')
  })
})

describe('TIME-13 "Nur diesen zeigen"', () => {
  it('TIME-13 AC-11 AC-12 Alt+click shows one calendar alone, also a hidden one; the bar says so; Alt+click again brings back the choice before', async () => {
    const lists = calendarList(CALENDARS.calendars.map((entry) => (entry.id === 7 ? { ...entry, hidden: true } : entry)))
    setPref('collection', 1, '8')
    await open({ calendars: lists })
    expect(toggle('Verein').title).toBe('Verein: nur diesen im Monat zeigen')
    await altClick(toggle('Verein'))
    expect(text('only-bar')).toBe('Nur Verein · andere Kalender vorübergehend ausgeblendetVorherige Auswahl zeigen')
    const asked = eventCalls().at(-1)!
    expect([asked.get('calendars'), asked.get('hidden')]).toEqual(['7', 'true'])
    // The collection and the hidden calendars are as they were.
    expect(text('collection-button')).toBe('SammlungFamilie ▾')
    expect(calls.filter((call) => call.method === 'PATCH')).toEqual([])
    await altClick(toggle('Verein'))
    expect(one('only-bar')).toBeNull()
    expect(eventCalls().at(-1)!.get('calendars')).toBe('1,2,4,6')
  })

  it('TIME-13 AC-11 AC-12 EC-21 the menu "⋯" offers it and then "Vorherige Auswahl zeigen"; so does the bar', async () => {
    await open()
    const more = () => box.querySelector<HTMLElement>('[aria-label="Mehr zu Familie"]')!
    await click(more())
    expect(text('calendar-only')).toBe('Nur diesen zeigen (Alt+Klick)')
    await click(one('calendar-only'))
    expect(one('only-bar')).not.toBeNull()
    // Background calendars are off too: only Familie is asked for.
    expect(eventCalls().at(-1)!.get('calendars')).toBe('1')
    await click(more())
    expect(text('calendar-only')).toBe('Vorherige Auswahl zeigen')
    await click(one('calendar-only'))
    expect(one('only-bar')).toBeNull()
    await click(more())
    await click(one('calendar-only'))
    await click(one('only-end'))
    expect(one('only-bar')).toBeNull()
  })

  it('TIME-13 click shows the calendar alone in the view on screen and the row chosen; again ends it, the view stays; the eye changes only the showing', async () => {
    await open({}, '/month')
    await click(toggle('Verein'))
    expect(one('view-month')).not.toBeNull()
    expect(one('only-bar')).not.toBeNull()
    expect(eventCalls().at(-1)!.get('calendars')).toBe('7')
    const row = toggle('Verein').closest('li')!
    expect(row.className).toContain('nt-chosen')
    expect(toggle('Verein').getAttribute('aria-pressed')).toBe('true')
    // Another calendar takes its place.
    await click(toggle('Arbeit'))
    expect(eventCalls().at(-1)!.get('calendars')).toBe('3')
    expect(toggle('Verein').closest('li')!.className).not.toContain('nt-chosen')
    // The same again: the choice before, the month stays.
    await click(toggle('Arbeit'))
    expect(one('only-bar')).toBeNull()
    expect(one('view-month')).not.toBeNull()
    // The eye: shown or hidden on the server, nothing opened, nothing shown alone.
    const eye = toggle('Persönlich').closest('li')!.querySelector<HTMLElement>('[data-testid="calendar-eye"]')!
    await click(eye)
    expect(calls.filter((call) => call.method === 'PATCH').map((call) => [call.path, call.body])).toEqual([['/api/calendars/2', { hidden: true }]])
    expect(one('only-bar')).toBeNull()
  })

  it('TIME-13 click keeps the view on screen (Heute, Woche, Jahr) and opens the month only from elsewhere', async () => {
    for (const [where, view] of [['/', 'view-today'], ['/week', 'view-week'], ['/year', 'view-year']] as const) {
      await open({}, where)
      await click(toggle('Verein'))
      expect(one(view)).not.toBeNull()
      expect(one('view-month')).toBeNull()
      expect(one('only-bar')).not.toBeNull()
      stop()
      start()
      forgetPrefs()
      localStorage.clear()
    }
    await open({}, '/search')
    await click(toggle('Verein'))
    expect(one('view-month')).not.toBeNull()
  })

  it('TIME-13 click opens month also from the drawer, and closes the drawer, also when the month is open already', async () => {
    await open({}, '/month')
    await click(one('profile-button'))
    const sheet = one('profile-sheet')!
    const inDrawer = [...sheet.querySelectorAll<HTMLElement>('[data-testid="calendar-toggle"]')].find((button) => button.textContent?.includes('Verein'))!
    await click(inDrawer)
    expect(one('view-month')).not.toBeNull()
    expect(one('only-bar')).not.toBeNull()
    expect(one('profile-sheet')).toBeNull()
  })

  it('TIME-13 AC-13 EC-18 a new event lands in the calendar shown alone; one that may only be read gets the usual one and a note', async () => {
    await open()
    await altClick(toggle('Verein'))
    await click(one('new-event'))
    expect((one('field-calendar') as HTMLSelectElement).value).toBe('7')
    await click(one('panel-close'))
    await altClick(toggle('Anna · Arbeit'))
    await click(one('new-event'))
    expect((one('field-calendar') as HTMLSelectElement).value).toBe('1')
    expect(one('event-panel')!.textContent).toContain('„Anna · Arbeit“ ist nur lesbar; der Termin kommt in einen anderen Kalender.')
    // Chosen by hand, the note has said its piece (review of the additions, 2026-11-23).
    await choose(one('field-calendar') as HTMLSelectElement, '2')
    expect(one('event-panel')!.textContent).not.toContain('ist nur lesbar; der Termin kommt')
  })

  it('TIME-13 EC-17 it holds over reloading and ends with signing out', async () => {
    await open()
    await altClick(toggle('Verein'))
    forgetPrefs()
    await open()
    expect(one('only-bar')).not.toBeNull()
    await click(one('settings-entry'))
    await click(one('sign-out'))
    await settle()
    expect(localStorage.getItem(prefKey('only', 1))).toBeNull()
  })

  it('TIME-13 EC-19 the calendar shown alone goes away: "Nur diesen zeigen" ends by itself', async () => {
    setPref('only', 1, '7')
    await open({ calendars: calendarList(CALENDARS.calendars.filter((entry) => entry.id !== 7)) })
    expect(one('only-bar')).toBeNull()
    expect(localStorage.getItem(prefKey('only', 1))).toBeNull()
    expect(eventCalls().at(-1)!.get('calendars')).toBeNull()
  })

  it('TIME-13 EC-1 a collection whose calendars are all gone or hidden says so', async () => {
    setPref('collection', 1, '8')
    await open({ calendars: calendarList([calendar(1, 'Familie', { default: true, hidden: true }), calendar(3, 'Arbeit', { color: 'teal' })]) }, '/week')
    expect(text('empty-collection')).toContain('In der Sammlung „Familie“ ist gerade kein Kalender zu sehen.')
  })
})

describe('TIME-13 preselect', () => {
  const press = async (key: string) => act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key })))
  const chosen = () => (one('field-calendar') as HTMLSelectElement).value

  it('TIME-13 preselect "Nur diesen" writable: key N, the month and the quick field take it, and the choice stays free', async () => {
    // Verein hidden in the list, as "Geburtstage" may be: shown alone it is still where new events go.
    const lists = calendarList(CALENDARS.calendars.map((entry) => (entry.id === 7 ? { ...entry, hidden: true } : entry)))
    await open({ calendars: lists })
    await altClick(toggle('Verein'))
    await press('n')
    expect(chosen()).toBe('7')
    // Still free to change.
    const select = one('field-calendar') as HTMLSelectElement
    expect([...select.options].map((option) => option.value)).toContain('2')
    await choose(select, '2')
    expect(chosen()).toBe('2')
    await click(one('panel-close'))
    await open({ calendars: lists }, '/month')
    await click(box.querySelector('[data-testid="month-day"][data-date="2026-12-03"]'))
    expect(chosen()).toBe('7')
    await click(one('panel-close'))
    await open({ calendars: lists })
    const field = one('quick-entry') as HTMLInputElement
    type(field, 'morgen 15 uhr zahnarzt')
    expect(text('quick-when')).toContain('Verein')
  })

  it('TIME-13 preselect "Nur diesen" read only: the usual calendar with the note, as before (EC-18)', async () => {
    await open()
    await altClick(toggle('Anna · Arbeit'))
    await press('n')
    expect(chosen()).toBe('1')
    expect(one('event-panel')!.textContent).toContain('„Anna · Arbeit“ ist nur lesbar')
  })

  it('TIME-13 preselect a collection: the usual calendar when it belongs, else the first writable one in the order of the list', async () => {
    setPref('collection', 1, '8')
    await open()
    await press('n')
    expect(chosen()).toBe('1')
    await click(one('panel-close'))
    setPref('collection', 1, '9')
    await open({}, '/month')
    await click(box.querySelector('[data-testid="month-day"][data-date="2026-12-03"]'))
    expect(chosen()).toBe('3')
  })
})

describe('TIME-13 preselect sidebar click', () => {
  const press = async (key: string) => act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key })))
  const chosen = () => (one('field-calendar') as HTMLSelectElement).value
  const BIRTHDAYS = { all_day: true, rule: 'yearly' as const, reminders: [360], busy: false }
  const withDefaults = () => calendarList(CALENDARS.calendars.map((entry) => (entry.id === 7 ? { ...entry, defaults: BIRTHDAYS } : entry)))

  it('TIME-13 preselect sidebar click then a click in the month: that calendar with its defaults', async () => {
    // Wide, as a wide screen: the column beside the month is there.
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query.includes('1600'), addEventListener: () => undefined, removeEventListener: () => undefined }))
    await open({ calendars: withDefaults(), more: (_method, path) => (path.startsWith('/api/search/calendar/') ? { status: 200, body: { total: 0, future_total: 0, offset: 0, items: [] } } : undefined) }, '/month')
    await click(toggle('Verein'))
    expect(one('view-month')).not.toBeNull()
    await click(box.querySelector('[data-testid="month-day"][data-date="2026-11-15"]'))
    expect(chosen()).toBe('7')
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('true')
    expect(one('field-rule')?.textContent).toContain('Jedes Jahr')
    expect([...document.querySelectorAll('[data-testid="reminder-chip"]')].map((chip) => chip.textContent)).toEqual(['am Vortag um 18:00×'])
  })

  it('TIME-13 preselect sidebar click then N, the plus, the week, the all-day row and the quick field', async () => {
    await open({ calendars: withDefaults() })
    await click(toggle('Verein'))
    await press('n')
    expect(chosen()).toBe('7')
    await click(one('panel-close'))
    await click(box.querySelector('[data-testid="bottom-plus"]'))
    expect(chosen()).toBe('7')
    await click(one('panel-close'))
    await press('w')
    const column = box.querySelectorAll<HTMLElement>('[data-testid="grid-column"]')[3]
    await act(async () => void column.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 200, clientY: 300, button: 0 })))
    await act(async () => void column.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, clientX: 200, clientY: 300, button: 0 })))
    await settle()
    expect(chosen()).toBe('7')
    await click(one('panel-close'))
    await click(box.querySelectorAll('[data-testid="all-day-cell"]')[2])
    expect(chosen()).toBe('7')
    await click(one('panel-close'))
    await press('h')
    type(one('quick-entry') as HTMLInputElement, 'morgen geburtstag oma')
    expect(text('quick-when')).toContain('Verein')
  })
})

describe('TIME-13 preselect late', () => {
  const press = async (key: string) => act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key })))
  const chosen = () => (one('field-calendar') as HTMLSelectElement).value
  const BIRTHDAYS = { all_day: true, rule: 'yearly' as const, reminders: [360], busy: false }
  const withDefaults = () => calendarList(CALENDARS.calendars.map((entry) => (entry.id === 7 ? { ...entry, defaults: BIRTHDAYS } : entry)))

  it('TIME-13 preselect sidebar click the choice arriving after the panel opened still moves the new event there, with its defaults', async () => {
    await open({ calendars: withDefaults() })
    await press('n')
    expect(chosen()).toBe('1')
    // What the browser showed: the panel's first look had the usual calendar, "Nur diesen" came a moment later.
    await act(async () => setPref('only', 1, '7'))
    await settle()
    expect(chosen()).toBe('7')
    expect(one('switch-all-day')?.getAttribute('aria-checked')).toBe('true')
    expect(one('field-rule')?.textContent).toContain('Jedes Jahr')
    // Nothing to ask when closing: the member changed nothing.
    await click(one('panel-close'))
    expect(one('event-panel')).toBeNull()
  })

  it('TIME-13 preselect sidebar click a calendar the member chose stays when the choice moves', async () => {
    await open({ calendars: withDefaults() })
    await press('n')
    await choose(one('field-calendar') as HTMLSelectElement, '2')
    await act(async () => setPref('only', 1, '7'))
    await settle()
    expect(chosen()).toBe('2')
  })
})
