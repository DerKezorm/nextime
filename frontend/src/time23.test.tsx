/** TIME-23 Trash on the page: the entry at the bottom of the sidebar, the deadline on top, entries with color, title,
 * date, calendar, who deleted it and when, the time left (orange in the last seven days); restoring an event, a
 * series and a calendar, asking for a target when its calendar is gone; emptying with a question; the empty trash;
 * many entries; code as text; the sentence about iCloud and Google. Now is Thursday, 26 November 2026, 16:05. */
import App from './App'
import type { TrashItem, TrashList } from './features/trash/api'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, choose, ok, render, start, stop } from './testing'
import { NOW, views } from './viewFakes'

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

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const text = (id: string) => one(id)?.textContent ?? ''

const common = { deleted_by: { self: true, name: 'Robin' }, days_left: 30, warn: false, expires_at: '2026-12-26T13:02:00+00:00' }

function eventItem(id: number, title: string, change: Partial<Extract<TrashItem, { kind: 'event' }>> = {}): TrashItem {
  return {
    kind: 'event', id, title, color: 'slate', deleted_at: '2026-11-26T13:02:00+00:00', ...common,
    calendar: { id: 1, name: 'Familie', source: '' }, calendar_gone: false, all_day: false, start: '2026-11-27T19:00:00+00:00',
    time_zone: 'Europe/Berlin', recurring: false, exceptions: false, ...change,
  } as TrashItem
}

const KINO = eventItem(5, 'Kino mit Anna', { deleted_by: { self: false, name: 'Anna' } })
const VEREIN: TrashItem = { kind: 'calendar', id: 9, title: 'Verein alt', color: 'green', events: 48, ...common, deleted_at: '2026-11-20T10:00:00+00:00', days_left: 24 }
const TRAINING = eventItem(6, 'Training', { color: 'green', calendar: { id: 7, name: 'Verein', source: '' }, start: '2026-11-02T16:00:00+00:00', recurring: true, exceptions: true, days_left: 3, warn: true, deleted_at: '2026-10-30T09:00:00+00:00' })
const SPRECHTAG = eventItem(8, 'Elternsprechtag', { calendar: { id: 12, name: 'Schule', source: '' }, calendar_gone: true, start: '2026-12-01T16:00:00+00:00', days_left: 29 })
const TEAM = eventItem(10, 'Teamrunde', { color: 'teal', calendar: { id: 3, name: 'Arbeit', source: 'icloud' }, deleted_at: '2026-11-25T08:00:00+00:00', days_left: 8 })

function listOf(items: TrashItem[], change: Partial<TrashList> = {}): TrashList {
  return { now: '2026-11-26T15:05:00+00:00', trash_days: 30, warn_days: 7, total: items.length, offset: 0, items, ...change }
}

async function open(list: (offset: number) => TrashList, more?: (method: string, path: string, body: unknown) => { status: number; body: unknown } | undefined, at = '/trash'): Promise<void> {
  views({
    rows: [],
    more: (method, path, body) =>
      more?.(method, path, body) ?? (path.startsWith('/api/trash?') ? ok(list(Number(new URL(path, 'http://x').searchParams.get('offset') ?? 0))) : undefined),
  })
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
}

const posts = () => calls.filter((call) => call.method === 'POST' && call.path.startsWith('/api/trash'))

describe('TIME-23 trash', () => {
  it('TIME-23 AC-1 the entry at the bottom of the sidebar leads to the trash', async () => {
    await open(() => listOf([KINO]), undefined, '/')
    const entry = one('trash-entry')!
    expect(entry.textContent).toBe('Papierkorb')
    expect(entry.compareDocumentPosition(one('settings-entry')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await click(entry)
    expect(one('trash-page')).not.toBeNull()
  })

  it('TIME-23 AC-1 AC-4 AC-7 the deadline on top; entries with color, title, date, calendar, who and when', async () => {
    await open(() => listOf([KINO, TEAM, VEREIN, TRAINING]))
    expect(box.querySelector('h1')!.textContent).toBe('Papierkorb')
    // The head as the spec has it (review of TIME-23, finding 5).
    expect(text('trash-keeps')).toBe('Gelöschtes bleibt 30 Tage')
    expect(all('trash-title').map((element) => element.textContent)).toEqual([
      'Kino mit Anna · Fr 27. Nov', 'Teamrunde · Fr 27. Nov', 'Kalender „Verein alt“ mit 48 Terminen', 'Training (Serie, ab 2. Nov)'])
    expect(all('trash-origin').map((element) => element.textContent)).toEqual([
      'Familie · gelöscht von Anna heute 14:02', 'Arbeit (iCloud) · gelöscht von dir gestern 09:00', 'gelöscht von dir am 20. Nov',
      'Verein · gelöscht von dir am 30. Okt, samt Ausnahmen'])
    expect(document.querySelector('[data-testid="trash-row"] [data-color]')!.getAttribute('data-color')).toBe('slate')
  })

  it('TIME-23 AC-6 the time left as text, orange in the last seven days', async () => {
    await open(() => listOf([eventItem(1, 'Acht', { days_left: 8 }), eventItem(2, 'Sieben', { days_left: 7, warn: true }), eventItem(3, 'Eins', { days_left: 1, warn: true }), eventItem(4, 'Heute', { days_left: 0, warn: true })]))
    const left = all('trash-left')
    expect(left.map((element) => element.textContent)).toEqual(['noch 8 Tage', 'noch 7 Tage', 'noch 1 Tag', 'geht heute endgültig'])
    expect(left.map((element) => element.className.includes('text-warn-500'))).toEqual([false, true, true, true])
  })

  it('TIME-23 AC-2 restoring an event: it is back, the entry goes, the views ask again', async () => {
    await open(() => listOf([KINO, VEREIN]), (method, path) => (method === 'POST' && path === '/api/trash/events/5/restore' ? ok({ id: 5, calendar_id: 1 }) : undefined))
    await click(all('trash-restore')[0])
    expect(posts().map((call) => call.path)).toEqual(['/api/trash/events/5/restore'])
    expect(text('toast')).toBe('„Kino mit Anna“ ist wieder da.')
    expect(all('trash-row')).toHaveLength(1)
  })

  it('TIME-23 AC-7 restoring a calendar', async () => {
    await open(() => listOf([VEREIN]), (method, path) => (method === 'POST' && path === '/api/trash/calendars/9/restore' ? ok({ id: 9 }) : undefined))
    await click(one('trash-restore'))
    expect(posts().map((call) => call.path)).toEqual(['/api/trash/calendars/9/restore'])
    expect(text('toast')).toBe('„Verein alt“ ist wieder da.')
  })

  it('TIME-23 EC-1 its calendar gone: said before, and restoring asks for a target', async () => {
    await open(() => listOf([SPRECHTAG]), (method, path) => (method === 'POST' && path.startsWith('/api/trash/events/8') ? ok({ id: 8, calendar_id: 3 }) : undefined))
    expect(text('trash-origin')).toBe('Schule · Kalender inzwischen gelöscht, fragt nach Ziel')
    expect(one('trash-origin')!.className).toContain('text-warn-500')
    await click(one('trash-restore'))
    expect(posts()).toHaveLength(0)
    expect(document.body.textContent).toContain('Wohin mit „Elternsprechtag“?')
    const select = one('trash-target') as HTMLSelectElement
    expect([...select.options].map((option) => option.textContent)).not.toContain('Anna · Arbeit')
    await choose(select, '3')
    await click(one('trash-target-go'))
    expect(posts()[0].body).toEqual({ calendar_id: 3 })
  })

  it('TIME-23 AC-3 "Papierkorb leeren" asks first, with the count, and takes what was there when the list came', async () => {
    await open(() => listOf([KINO, VEREIN]), (method, path) => (method === 'POST' && path === '/api/trash/empty' ? ok({ events: 1, calendars: 1 }) : undefined))
    await click(one('trash-empty'))
    expect(posts()).toHaveLength(0)
    expect(text('trash-empty-dialog')).toContain('Die 2 Einträge sind danach endgültig weg, auch mit ihren Anhängen.')
    expect(text('trash-empty-dialog')).toContain('Das lässt sich nicht rückgängig machen.')
    await click(one('trash-empty-go'))
    expect(posts()[0].body).toEqual({ until: '2026-11-26T15:05:00+00:00' })
    expect(text('toast')).toBe('Papierkorb geleert.')
  })

  it('TIME-23 EC-3 the empty trash explains itself and cannot be emptied', async () => {
    await open(() => listOf([]))
    expect(text('trash-nothing')).toContain('Der Papierkorb ist leer')
    expect(text('trash-nothing')).toContain('Gelöschte Termine und Kalender bleiben hier 30 Tage, dann sind sie endgültig weg.')
    expect((one('trash-empty') as HTMLButtonElement).disabled).toBe(true)
  })

  it('TIME-23 AC-5 the sentence about iCloud and Google', async () => {
    await open(() => listOf([TEAM]))
    expect(text('trash-sources')).toBe('iCloud- und Google-Termine, die du in nextime gelöscht hast, legt „Wiederherstellen“ dort neu an. Was direkt in Apple Kalender gelöscht wurde, steht hier nicht.')
  })

  it('TIME-23 EC-4 many entries come page by page; a long title is cut, whole in its tooltip', async () => {
    const long = 'Ä'.repeat(200)
    const page = (offset: number) => Array.from({ length: 100 }, (_, index) => eventItem(5000 - offset - index, index === 0 && offset === 0 ? long : `Import ${offset + index}`))
    await open((offset) => listOf(page(offset), { total: 5000, offset }))
    expect(all('trash-row')).toHaveLength(100)
    expect(all('trash-title')[0].getAttribute('title')).toBe(`${long} · Fr 27. Nov`)
    expect(all('trash-title')[0].className).toContain('truncate')
    await click(one('trash-more'))
    expect(all('trash-row')).toHaveLength(200)
    expect(calls.filter((call) => call.path.startsWith('/api/trash?')).map((call) => new URL(call.path, 'http://x').searchParams.get('offset'))).toEqual(['0', '100'])
  })

  it('TIME-23 EC-5 titles and names of others with code are text', async () => {
    await open(() => listOf([eventItem(1, '<img src=x onerror=alert(1)>', { calendar: { id: 1, name: '<b>Familie</b>', source: '' }, deleted_by: { self: false, name: '<script>x</script>' } })]))
    expect(text('trash-title')).toBe('<img src=x onerror=alert(1)> · Fr 27. Nov')
    expect(text('trash-origin')).toBe('<b>Familie</b> · gelöscht von <script>x</script> heute 14:02')
    expect(box.querySelector('[data-testid="trash-row"] img, [data-testid="trash-row"] b, [data-testid="trash-row"] script')).toBeNull()
  })

  it('TIME-23 EC-6 an entry restored meanwhile elsewhere says so and the list comes anew', async () => {
    let restored = false
    await open(() => listOf(restored ? [] : [KINO]), (method, path) => {
      if (method === 'POST' && path.startsWith('/api/trash/events/5')) {
        restored = true
        return { status: 404, body: { detail: { code: 'not_in_trash', message: 'x' } } }
      }
      return undefined
    })
    await click(one('trash-restore'))
    expect(text('trash-problem')).toBe('Das liegt nicht mehr im Papierkorb: schon wiederhergestellt oder endgültig gelöscht.')
    expect(one('trash-nothing')).not.toBeNull()
  })

  it('TIME-23 EC-14 the deletion in the zone of the member: 23:30 in UTC is tomorrow in Berlin', async () => {
    await open(() => listOf([eventItem(1, 'Spät', { deleted_at: '2026-11-25T23:30:00+00:00' })]))
    expect(text('trash-origin')).toBe('Familie · gelöscht von dir heute 00:30')
  })

  it('TIME-23 AC-4 TIME-5 AC-6 an occurrence and the following of a series are entries and come back', async () => {
    const one: TrashItem = { ...(eventItem(21, 'Teamrunde', { start: '2026-11-19T09:00:00+00:00' }) as object), kind: 'part', part: 'occurrence', series_gone: false } as TrashItem
    const following: TrashItem = { ...(eventItem(22, 'Training', { start: '2026-12-03T16:00:00+00:00', recurring: true, exceptions: true }) as object), kind: 'part', part: 'following', series_gone: false } as TrashItem
    await open(() => listOf([one, following]), (method, path) => (method === 'POST' && path.startsWith('/api/trash/parts/') ? ok({ id: 7, calendar_id: 1 }) : undefined))
    expect(all('trash-title').map((element) => element.textContent)).toEqual(['Teamrunde · Do 19. Nov', 'Training (Serie, ab 3. Dez)'])
    expect(all('trash-origin')[1].textContent).toContain(', samt Ausnahmen')
    await click(all('trash-restore')[0])
    expect(posts().map((call) => call.path)).toEqual(['/api/trash/parts/21/restore'])
    expect(text('toast')).toBe('„Teamrunde“ ist wieder da.')
  })

  it('TIME-23 AC-4 a part whose series is in the trash says so and the server\'s sentence comes on restore', async () => {
    const waiting: TrashItem = { ...(eventItem(23, 'Training') as object), kind: 'part', part: 'occurrence', series_gone: true } as TrashItem
    await open(() => listOf([waiting]), (method, path) =>
      method === 'POST' && path.startsWith('/api/trash/parts/') ? { status: 409, body: { detail: { code: 'series_gone', message: 'x' } } } : undefined)
    expect(text('trash-origin')).toBe('Familie · Serie im Papierkorb oder Kalender gelöscht: zuerst die Serie wiederherstellen')
    expect(one('trash-origin')!.className).toContain('text-warn-500')
    await click(one('trash-restore'))
    expect(document.body.textContent).not.toContain('Wohin mit')
    expect(text('trash-problem')).toBe('Seine Serie ist nicht mehr da. Stelle zuerst die Serie wieder her.')
  })
})
