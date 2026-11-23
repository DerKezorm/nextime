/** TIME-22 Search on the page: the field in the sidebar with Ctrl K, "Suchen" at the bottom on a phone, hits while
 * typing with the count, coming and past, date, dot, title and calendar; a hit opens its event on its day; nothing
 * found in a collection and "In allen Kalendern suchen"; a series once; declined marked; a source behind; code as
 * text; the device's zone; more on demand. Now is Thursday, 26 November 2026, 16:05 in Berlin. */
import { act } from 'react'

import App from './App'
import type { Found, Hit } from './features/search/api'
import i18n from './i18n'
import { forgetPrefs, setPref } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, ok, render, settle, start, stop, type } from './testing'
import { NOW, views } from './viewFakes'

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
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

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const text = (id: string) => one(id)?.textContent ?? ''

function hit(id: number, title: string, startAt: string, change: Partial<Hit> = {}): Hit {
  const allDay = startAt.length === 10
  return { id, occurrence: null, calendar_id: 1, title, recurring: false, declined: false, waiting: false, coming: true, all_day: allDay, start: startAt, end: startAt, time_zone: allDay ? '' : 'Europe/Berlin', ...change }
}

function found(items: Hit[], change: Partial<Found> = {}): Found {
  return { total: items.length, future_total: items.filter((item) => item.coming).length, offset: 0, items, behind: [], ...change }
}

const ZAHNARZT = found([
  hit(11, 'Zahnarzt Mia', '2026-11-26T15:00:00Z'),
  hit(12, 'Abholen', '2026-11-26T15:30:00Z', { calendar_id: 2 }),
  hit(13, 'Zahnspange tragen', '2026-12-01T18:00:00Z', { recurring: true, occurrence: '20261201T180000Z' }),
  hit(14, 'Zahnarzt Jahrescheck', '2036-01-08T07:00:00Z'),
  hit(15, 'Zahnarzt heute früh', '2026-11-26T07:00:00Z', { coming: false }),
  hit(16, 'Kontrolle', '2025-10-14T08:00:00Z', { coming: false, calendar_id: 3 }),
])

const searches = () => calls.filter((call) => call.path.startsWith('/api/search'))
const query = (index = -1) => new URL(searches().at(index)!.path, 'http://x').searchParams

async function open(answer: (params: URLSearchParams) => Found, at = '/', more?: (method: string, path: string) => { status: number; body: unknown } | undefined): Promise<void> {
  views({
    rows: [],
    more: (method, path) => more?.(method, path) ?? (path.startsWith('/api/search') ? ok(answer(new URL(path, 'http://x').searchParams)) : undefined),
  })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
}

async function ask(q: string): Promise<void> {
  type(one('search-field') as HTMLInputElement, q)
  await act(async () => void vi.advanceTimersByTime(200))
  await settle()
}

describe('TIME-22 search', () => {
  it('TIME-22 AC-1 the field "Suchen" in the sidebar and Ctrl K lead to the search; it asks with the device\'s now and zone', async () => {
    await open(() => ZAHNARZT)
    expect(one('sidebar-search')!.getAttribute('href')).toBe('/search')
    expect(text('search-key')).toBe('Strg K')
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true })))
    await settle()
    expect(one('search-page')).not.toBeNull()
    expect(text('search-hint')).toContain('Titel, Ort und Notizen')
    await ask('zahnarzt')
    expect(query().get('q')).toBe('zahnarzt')
    expect(query().get('now')).toMatch(/^2026-11-26T15:05:00/)
    expect(query().get('zone')).toBe('Europe/Berlin')
    expect(query().has('calendars')).toBe(false)
    expect(all('search-hit')).toHaveLength(6)
  })

  it('TIME-22 AC-1 the keys "/" and Ctrl Shift F open it too, but "/" not while typing', async () => {
    await open(() => ZAHNARZT)
    const quick = one('quick-entry') as HTMLInputElement
    await act(async () => void quick.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true })))
    expect(one('search-page')).toBeNull()
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: '/' })))
    await settle()
    expect(one('search-page')).not.toBeNull()
    await click(one('sidebar-places')!.querySelector('a[href="/"]'))
    await act(async () => void window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F', ctrlKey: true, shiftKey: true })))
    await settle()
    expect(one('search-page')).not.toBeNull()
  })

  it('TIME-22 AC-2 AC-5 EC-10 the count, "Künftig" with the running one on top, "Vergangen" with the latest on top', async () => {
    await open(() => ZAHNARZT, '/search')
    await ask('zahnarzt')
    expect(text('search-count')).toBe('6 Treffer in „Alles“ · Titel, Ort und Notizen')
    const coming = [...one('search-coming')!.querySelectorAll('[data-testid="search-hit-title"]')].map((element) => element.textContent)
    const past = [...one('search-past')!.querySelectorAll('[data-testid="search-hit-title"]')].map((element) => element.textContent)
    expect(coming).toEqual(['Zahnarzt Mia', 'Abholen', 'Zahnspange tragen', 'Zahnarzt Jahrescheck'])
    expect(past).toEqual(['Zahnarzt heute früh', 'Kontrolle'])
    expect(all('search-hit-date').map((element) => element.textContent)).toEqual([
      'heute, 16:00', 'heute, 16:30', 'nächster Termin am Di, 1. Dez', 'Di, 8. Jan 2036', 'heute, 08:00', 'Di, 14. Okt 2025'])
    expect(all('search-hit-calendar').map((element) => element.textContent)).toEqual(['Familie', 'Persönlich', 'Familie', 'Familie', 'Familie', 'Arbeit'])
    expect(document.querySelector('[data-testid="search-hit"] [data-color]')!.getAttribute('data-color')).toBe('slate')
    expect(one('search-page')!.querySelectorAll('h2')[0].textContent).toBe('Künftig')
  })

  it('TIME-22 AC-3 a hit opens its event and the view jumps to its day', async () => {
    await open(() => ZAHNARZT, '/search')
    await ask('zahnarzt')
    await click(all('search-hit')[3])
    expect(one('view-day') ?? one('view-grid') ?? one('grid')).not.toBeNull()
    expect(document.body.textContent).toContain('Januar 2036')
    const opened = calls.filter((call) => /^\/api\/events\/14/.test(call.path))
    expect(opened.length).toBeGreaterThan(0)
  })

  it('TIME-22 AC-3 Enter in the field opens the first hit', async () => {
    await open(() => ZAHNARZT, '/search')
    await ask('zahnarzt')
    await act(async () => void (one('search-field') as HTMLInputElement).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
    await settle()
    expect(calls.some((call) => /^\/api\/events\/11/.test(call.path))).toBe(true)
  })

  it('TIME-22 AC-6 nothing in the collection: the dashed box, and "In allen Kalendern suchen" asks again over all', async () => {
    setPref('collection', 1, '9')
    await open(
      (params) => (params.has('calendars') ? found([]) : found([hit(21, 'Steuerberater', '2026-12-02T09:00:00Z', { calendar_id: 3 })])),
      '/search',
      (_method, path) => (path.startsWith('/api/collections') ? ok({ collections: [{ id: 9, name: 'Familie', calendar_ids: [1, 2], updated_at: '' }] }) : undefined),
    )
    await ask('Steuerberater')
    expect(query().get('calendars')).toBe('1,2')
    expect(text('search-empty-title')).toBe('Nichts zu „Steuerberater“')
    expect(text('search-empty-where')).toBe('gesucht in der Sammlung „Familie“')
    expect(one('search-empty')!.className).toContain('border-dashed')
    await click(one('search-everywhere'))
    await act(async () => void vi.advanceTimersByTime(200))
    await settle()
    expect(query().has('calendars')).toBe(false)
    expect(text('search-count')).toBe('1 Treffer in allen Kalendern · Titel, Ort und Notizen')
  })

  it('TIME-22 AC-6 in "Alles" with nothing found there is no "In allen Kalendern suchen"', async () => {
    await open(() => found([]), '/search')
    await ask('Steuerberater')
    expect(text('search-empty-where')).toBe('gesucht in allen Kalendern')
    expect(one('search-everywhere')).toBeNull()
  })

  it('TIME-22 EC-3 an empty field or blanks ask nothing and show no list', async () => {
    await open(() => ZAHNARZT, '/search')
    await ask('   ')
    expect(searches()).toHaveLength(0)
    expect(one('search-hint')).not.toBeNull()
    await ask('  zahnarzt  ')
    expect(query().get('q')).toBe('zahnarzt')
  })

  it('TIME-22 EC-5 code in a title is text', async () => {
    await open(() => found([hit(31, '<img src=x onerror=alert(1)>', '2026-12-02T09:00:00Z')]), '/search')
    await ask('img')
    expect(text('search-hit-title')).toBe('<img src=x onerror=alert(1)>')
    expect(box.querySelector('[data-testid="search-hit"] img')).toBeNull()
  })

  it('TIME-22 EC-12 date and time in the zone of the device', async () => {
    await open(() => found([hit(41, 'Call New York', '2026-12-03T23:30:00Z', { time_zone: 'America/New_York' })]), '/search')
    await ask('call')
    expect(text('search-hit-date')).toBe('Fr, 4. Dez')
  })

  it('TIME-22 EC-15 a declined invitation is marked "abgesagt"', async () => {
    await open(() => found([hit(51, 'Planung Weihnachtsfeier', '2026-12-10T16:00:00Z', { declined: true })]), '/search')
    await ask('planung')
    expect(text('search-declined')).toBe('abgesagt')
  })

  it('TIME-22 EC-8 a source that does not answer is named with its state', async () => {
    await open(() => found([hit(61, 'Teamrunde', '2026-12-10T09:00:00Z')], { behind: [{ kind: 'icloud', label: 'robin@example.com', synced_at: '2026-11-26T14:52:00Z' }] }), '/search')
    await ask('team')
    expect(text('search-count')).toBe('1 Treffer in „Alles“ · Titel, Ort und Notizen · iCloud mit Stand von 15:52')
  })

  it('TIME-22 EC-11 many hits come a page at a time, the count is all of them', async () => {
    const page = (offset: number) => Array.from({ length: 100 }, (_, index) => hit(1000 + offset + index, `Termin ${offset + index}`, '2026-12-01T09:00:00Z'))
    await open((params) => found(page(Number(params.get('offset'))), { total: 250, future_total: 250 }), '/search')
    await ask('e')
    expect(text('search-count')).toBe('250 Treffer in „Alles“ · Titel, Ort und Notizen')
    expect(all('search-hit')).toHaveLength(100)
    expect(text('search-more')).toBe('150 weitere laden')
    await click(one('search-more'))
    expect(query().get('offset')).toBe('100')
    expect(all('search-hit')).toHaveLength(200)
  })

  it('TIME-22 AC-1 an older answer arriving after a newer one is dropped', async () => {
    const waiting: Record<string, (value: Response) => void> = {}
    await open(() => ZAHNARZT, '/search')
    const original = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (path.startsWith('/api/search')) {
        calls.push({ method: 'GET', path, body: undefined, headers: {} })
        const q = new URL(path, 'http://x').searchParams.get('q')!
        return new Promise<Response>((resolve) => (waiting[q] = resolve))
      }
      return original(input, init)
    }))
    const answer = (q: string) =>
      act(async () => waiting[q](new Response(JSON.stringify(found([hit(81, `Treffer ${q}`, '2026-12-01T09:00:00Z')])), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    await ask('zahn')
    await ask('zahnarzt')
    await answer('zahnarzt')
    await settle()
    await answer('zahn')
    await settle()
    expect(all('search-hit-title').map((element) => element.textContent)).toEqual(['Treffer zahnarzt'])
  })

  it('TIME-22 EC-13 on a phone "Suchen" stands in the bar at the bottom', async () => {
    await open(() => ZAHNARZT)
    const link = one('bottom-search')!
    expect(link.textContent).toBe('Suchen')
    expect(link.getAttribute('href')).toBe('/search')
  })

  it('TIME-22 Escape empties the field, then leaves; an older answer arriving late is dropped', async () => {
    await open((params) => found([hit(71, `Treffer ${params.get('q')}`, '2026-12-01T09:00:00Z')]), '/search')
    await ask('zahn')
    expect(text('search-hit-title')).toBe('Treffer zahn')
    const field = one('search-field') as HTMLInputElement
    await act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    await settle()
    expect(field.value).toBe('')
    await act(async () => void (one('search-field') as HTMLInputElement).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    await settle()
    expect(one('search-page')).toBeNull()
  })

  it('TIME-22 AC-2 a series whose next occurrence is today says "nächster Termin am", not "heute"', async () => {
    await open(() => found([hit(91, 'Training', '2026-11-26T17:00:00Z', { recurring: true, occurrence: '20261126T170000Z' })]), '/search')
    await ask('training')
    expect(text('search-hit-date')).toBe('nächster Termin am Do, 26. Nov')
  })

  it('TIME-22 AC-5 EC-11 further pages ask with the "now" of the first, so that no hit slips between them', async () => {
    const page = (offset: number) => Array.from({ length: 100 }, (_, index) => hit(2000 + offset + index, `Termin ${offset + index}`, '2026-12-01T09:00:00Z'))
    await open((params) => found(page(Number(params.get('offset'))), { total: 250, future_total: 250 }), '/search')
    await ask('e')
    vi.setSystemTime(new Date('2026-11-26T17:00:00Z'))
    await click(one('search-more'))
    const asked = searches().map((call) => new URL(call.path, 'http://x').searchParams.get('now'))
    expect(asked).toHaveLength(2)
    expect(asked[1]).toBe(asked[0])
  })
})
