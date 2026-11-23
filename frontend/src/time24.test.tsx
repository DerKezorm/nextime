/** TIME-24 Export, import and moving house on the page: "Mitnehmen und umziehen" with its three cards, the downloads,
 * loading a file with its preview, the calendar chosen and the result, moving a calendar from iCloud with its result
 * and the sentence about the source, what comes first without an own calendar or a connection, and "Datei laden" in
 * "Kalender hinzufügen". The server is a stand-in (`viewFakes`). */
import { act } from 'react'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import { markUnreachable, resetConnection } from './features/offline/connection'
import { periodText } from './features/transfer/period'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { AuthProvider } from './state/auth'
import { box, calls, click, ok, render, settle, start, stop } from './testing'
import { CALENDARS, NOW, views, type Fake } from './viewFakes'

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const text = (id: string) => one(id)?.textContent ?? ''

const PREVIEW = {
  id: 'a'.repeat(32), name: 'familie-2025.ics', events: 212, first: '2025-01-03', last: '2025-12-28', skipped: 0,
  too_large: 0, existing: 3, new: 209, account_events: 400, limit: 20000, over_limit: false, state: 'ready', result: null,
}

const WITH_ICLOUD = calendarList([
  ...CALENDARS.calendars,
  calendar(20, 'Persönlich', { group: 'icloud', color: 'orange', source: 'iCloud' }),
])

/** A job of the server that is done at once, with its result. */
function done(result: object) {
  return { state: 'done', done: 1, total: 1, problem: null, result }
}

function server(change: { preview?: object; movable?: object[]; calendars?: Fake['calendars']; more?: Fake['more'] } = {}): Fake {
  return {
    rows: [],
    calendars: change.calendars ?? WITH_ICLOUD,
    more: (method, path, body) => {
      const extra = change.more?.(method, path, body)
      if (extra) return extra
      if (path === '/api/moves') return ok({ calendars: change.movable ?? [{ id: 20, name: 'Persönlich', color: 'orange', kind: 'icloud' }] })
      if (path === '/api/calendars/20/move' && method === 'GET')
        return ok({ calendar: { id: 20, name: 'Persönlich', color: 'orange' }, kind: 'icloud', events: 188, invitations: 2, moved_to: null, shared: false, state: 'ok', account_events: 300, limit: 20000, over_limit: false })
      if (path === '/api/calendars/20/move' && method === 'POST')
        return ok(done({ made: 186, existing: 0, failed: [{ title: 'Urlaub', reason: 'attachment_too_large' }, { title: 'Reise', reason: 'attachment_too_large' }], invitations: 2, again: false, kind: 'icloud', shared: false, from: { id: 20, name: 'Persönlich' }, calendar: { id: 30, name: 'Persönlich' } }))
      if (path.startsWith('/api/imports?') && method === 'POST') return { status: 201, body: change.preview ?? PREVIEW }
      if (path.startsWith(`/api/imports/${PREVIEW.id}?`)) return ok(change.preview ?? PREVIEW)
      if (path === `/api/imports/${PREVIEW.id}/apply`) return ok(done({ made: 209, existing: 3, failed: [], calendar: { id: 1, name: 'Familie' } }))
      return undefined
    },
  }
}

async function open(fake: Fake = server(), at = '/settings/move'): Promise<void> {
  views(fake)
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
  await settle()
}

async function pick(name: string, content = 'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n'): Promise<void> {
  const input = one('load-file') as HTMLInputElement
  Object.defineProperty(input, 'files', { configurable: true, value: [new File([content], name, { type: 'text/calendar' })] })
  await act(async () => void input.dispatchEvent(new Event('change', { bubbles: true })))
  await settle()
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  localStorage.clear()
  resetConnection()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  resetConnection()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('TIME-24 Mitnehmen und umziehen', () => {
  it('TIME-24 AC-5 EC-15 the settings have "Mitnehmen und umziehen" with three cards, one under the other on a phone', async () => {
    await open()
    expect(box.querySelector('h1')?.textContent ?? box.textContent).toContain('Mitnehmen und umziehen')
    expect(['move-export-title', 'move-load-title', 'move-icloud-title'].map((id) => box.querySelector(`#${id}`)?.textContent)).toEqual([
      'Exportieren',
      'Datei laden',
      'Aus iCloud umziehen',
    ])
    // The cards stand under each other on every width, like the settings of nexnotes.
    expect(one('move-area')!.className).toContain('flex-col')
    expect(one('move-area')!.className).not.toMatch(/grid-cols-/)
  })

  it('TIME-24 AC-5 "Datei laden" in "Kalender hinzufügen" leads to the same loading', async () => {
    await open(server(), '/')
    await click(one('add-calendar'))
    const way = one('way-file') as HTMLButtonElement
    expect(way.disabled).toBe(false)
    expect(way.textContent).not.toContain('kommt')
    await click(way)
    expect(one('move-area')).not.toBeNull()
    expect(one('load-file')).not.toBeNull()
  })

  it('TIME-24 AC-1 a calendar downloads as ICS, all of them as a ZIP with their number', async () => {
    await open()
    const download = one('export-download') as HTMLAnchorElement
    expect(download.textContent).toBe('Herunterladen')
    expect(download.getAttribute('href')).toBe('/api/calendars/1/export.ics')
    expect(download.hasAttribute('download')).toBe(true)
    const select = one('export-calendar') as HTMLSelectElement
    expect([...select.options].map((option) => option.textContent)).toContain('Persönlich')
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, '20')
    await act(async () => void select.dispatchEvent(new Event('change', { bubbles: true })))
    expect((one('export-download') as HTMLAnchorElement).getAttribute('href')).toBe('/api/calendars/20/export.ics')
    expect(text('export-zip')).toBe(`Alles als ZIP (${WITH_ICLOUD.calendars.length} Kalender)`)
    expect(one('export-zip')!.getAttribute('href')).toBe('/api/export.zip')
  })

  it('TIME-24 AC-2 a file shows name, number, period and what is there; "nach …" own calendars only; the button names what comes', async () => {
    await open()
    await pick('familie-2025.ics')
    const upload = calls.find((call) => call.method === 'POST' && call.path.startsWith('/api/imports?'))!
    expect(new URL(upload.path, 'http://x').searchParams.get('name')).toBe('familie-2025.ics')
    expect(text('load-name')).toBe('familie-2025.ics')
    expect(text('load-count')).toBe('212 Termine · Januar bis Dezember 2025')
    expect(text('load-existing')).toBe('3 sind schon da und werden übersprungen')
    const target = one('load-target') as HTMLSelectElement
    expect([...target.options].map((option) => option.textContent)).toEqual(['nach Familie', 'nach Persönlich', 'nach Arbeit', 'nach Feiertage', 'nach Müllabfuhr', 'nach Verein'])
    expect(text('load-apply')).toBe('209 Termine übernehmen')
    await click(one('load-apply'))
    expect(calls.filter((call) => call.path.endsWith('/apply')).map((call) => call.body)).toEqual([{ calendar_id: 1 }])
    expect(text('load-result')).toBe('209 Termine nach „Familie“ übernommen. 3 waren schon da.')
    expect(box.textContent).toContain('Datei wählen …')
  })

  it('TIME-24 EC-1 EC-5 a file that is no calendar says so; names stay text', async () => {
    await open(server({ more: (method, path) => (method === 'POST' && path.startsWith('/api/imports?') ? { status: 422, body: { detail: { code: 'import_not_calendar' } } } : undefined) }))
    await pick('<b>seite</b>.html', '<html></html>')
    expect(text('load-problem')).toBe('Diese Datei ist kein Kalender. Wähle eine ICS-Datei.')
    expect(one('load-preview')).toBeNull()
    await open(server({ preview: { ...PREVIEW, name: '<img src=x onerror=alert(1)>.ics' } }))
    await pick('x.ics')
    expect(text('load-name')).toBe('<img src=x onerror=alert(1)>.ics')
    expect(box.querySelector('img[src="x"]')).toBeNull()
  })

  it('TIME-24 EC-2 EC-6 EC-7 too many events warn before; entries of other kinds and files too large are named', async () => {
    await open(server({ preview: { ...PREVIEW, over_limit: true, account_events: 19950, skipped: 4, too_large: 1 } }))
    await pick('gross.ics')
    expect(text('load-limit')).toBe('Danach hast du 20159 Termine; ab 20000 kann nextime langsamer werden.')
    expect(text('load-skipped')).toBe('4 Einträge sind keine Termine und werden übersprungen')
    expect(text('load-too-large')).toBe('1 Anhang ist größer als erlaubt und kommt nicht mit')
  })

  it('TIME-24 EC-10 EC-11 two clicks send it once; a file whose events are all there takes nothing', async () => {
    await open()
    await pick('familie-2025.ics')
    const button = one('load-apply') as HTMLButtonElement
    await act(async () => {
      button.click()
      button.click()
    })
    await settle()
    expect(calls.filter((call) => call.path.endsWith('/apply'))).toHaveLength(1)
    await open(server({ preview: { ...PREVIEW, existing: 212, new: 0 } }))
    await pick('familie-2025.ics')
    expect((one('load-apply') as HTMLButtonElement).disabled).toBe(true)
    expect(text('load-apply')).toBe('Alle Termine sind schon da')
  })

  it('TIME-24 AC-3 AC-4 AC-8 moving names "Persönlich (iCloud) → Persönlich", gives the result with reasons and leaves iCloud as it is', async () => {
    await open()
    const select = one('move-calendar') as HTMLSelectElement
    expect([...select.options].map((option) => option.textContent)).toEqual(['Persönlich (iCloud) → Persönlich'])
    expect(text('move-preview')).toContain('188 Termine ziehen um.')
    expect(text('move-preview')).toContain('2 Einladungen sind nicht zugesagt und bleiben, wo sie sind.')
    await click(one('move-go'))
    const sent = calls.filter((call) => call.method === 'POST' && call.path === '/api/calendars/20/move')
    expect(sent).toHaveLength(1)
    expect((sent[0].body as { client_ref: string }).client_ref).toMatch(/^ref-/)
    expect(text('move-done')).toBe('186 Termine umgezogen. 2 nicht: Anhang über 25 MB.')
    expect(text('move-stays')).toBe('In iCloud bleibt alles unverändert; der alte Kalender ist in nextime jetzt ausgeblendet.')
    expect(text('move-invitations')).toBe('2 Einladungen (offen, vielleicht oder abgesagt) sind nicht mitgekommen; sie bleiben in iCloud.')
    expect(text('move-result')).toContain('Persönlich (iCloud)')
  })

  it('TIME-24 EC-12 a shared calendar says the old share stays and the new one is not shared', async () => {
    await open(server({ more: (method, path) => (path === '/api/calendars/20/move' && method === 'POST' ? ok(done({ made: 3, existing: 0, failed: [], invitations: 0, again: false, kind: 'icloud', shared: true, from: { id: 20, name: 'Persönlich' }, calendar: { id: 30, name: 'Persönlich' } })) : undefined) }))
    await click(one('move-go'))
    expect(text('move-shared')).toBe('Der alte Kalender bleibt geteilt, wie er war. Der neue ist nicht geteilt; teile ihn, wenn andere ihn sehen sollen.')
  })

  it('TIME-24 EC-13 Google moves as well, in a card of its own', async () => {
    await open(server({ movable: [{ id: 21, name: 'Elternbeirat', color: 'teal', kind: 'google' }], more: (method, path) => (path === '/api/calendars/21/move' && method === 'GET' ? ok({ calendar: { id: 21, name: 'Elternbeirat', color: 'teal' }, kind: 'google', events: 4, invitations: 0, moved_to: { id: 31, name: 'Elternbeirat' }, shared: false, state: 'ok', account_events: 10, limit: 20000, over_limit: false }) : undefined) }))
    expect(box.querySelector('#move-google-title')?.textContent).toBe('Aus Google umziehen')
    expect([...(one('move-calendar') as HTMLSelectElement).options].map((option) => option.textContent)).toEqual(['Elternbeirat (Google) → Elternbeirat'])
    expect(text('move-again')).toBe('Schon umgezogen nach „Elternbeirat“: Was dort schon ist, wird übersprungen.')
  })

  it('TIME-24 EC-16 without an own calendar or a connected iCloud or Google it says what comes first', async () => {
    const onlyShared = calendarList([calendar(4, 'Anna · Arbeit', { group: 'shared', right: 'read', writable: false })])
    await open(server({ calendars: onlyShared, movable: [] }))
    expect(text('load-needs-calendar')).toBe('Eine Datei kommt in einen eigenen Kalender. Lege zuerst einen an.')
    expect(text('load-new-calendar')).toBe('Neuer Kalender')
    expect(one('load-file')).toBeNull()
    expect(text('move-needs-source')).toBe('Kein iCloud- oder Google-Kalender verbunden. Verbinde zuerst einen über „Kalender hinzufügen“.')
    await click(one('move-add-calendar'))
    expect(one('add-calendar-dialog')).not.toBeNull()
  })

  it('TIME-24 EC-9 a move that did not work says why and can start again with the same reference', async () => {
    let fail = true
    await open(server({ more: (method, path) => (path === '/api/calendars/20/move' && method === 'POST' && fail ? { status: 409, body: { detail: { code: 'move_source_unreachable' } } } : undefined) }))
    await click(one('move-go'))
    expect(text('move-problem')).toBe('Die Quelle antwortet gerade nicht. Es wurde nichts umgezogen; versuche es später noch einmal.')
    fail = false
    await click(one('move-go'))
    const refs = calls.filter((call) => call.method === 'POST' && call.path === '/api/calendars/20/move').map((call) => (call.body as { client_ref: string }).client_ref)
    expect(refs).toHaveLength(2)
    expect(refs[0]).toBe(refs[1])
    expect(one('move-done')).not.toBeNull()
  })

  it('TIME-24 AC-2 the period reads as months', () => {
    expect(periodText('2025-01-03', '2025-12-28', 'de', ' bis ')).toBe('Januar bis Dezember 2025')
    expect(periodText('2024-03-01', '2026-05-02', 'de', ' bis ')).toBe('März 2024 bis Mai 2026')
    expect(periodText('2025-10-14', '2025-10-14', 'de')).toBe('14. Oktober 2025')
    expect(periodText('2025-10-01', '2025-10-14', 'de')).toBe('Oktober 2025')
  })

  it('TIME-24 EC-2 EC-9 taking a large file shows how far it is and ends with the result', async () => {
    let asked = 0
    await open(server({
      more: (method, path) => {
        if (path === `/api/imports/${PREVIEW.id}/apply`) return ok({ state: 'running', done: 0, total: 212, problem: null, result: null })
        if (path === `/api/imports/${PREVIEW.id}` && method === 'GET') {
          asked += 1
          return ok({ ...PREVIEW, state: 'done', done: 212, total: 212, result: { made: 209, existing: 3, shortened: 2, failed: [], calendar: { id: 1, name: 'Familie' } } })
        }
        return undefined
      },
    }))
    await pick('familie-2025.ics')
    await act(async () => (one('load-apply') as HTMLButtonElement).click())
    expect(text('job-progress')).toBe('Übernimmt … 0 von 212')
    await act(() => new Promise<void>((resolve) => setTimeout(resolve, 1100)))
    await settle()
    expect(asked).toBe(1)
    expect(text('load-result')).toBe('209 Termine nach „Familie“ übernommen. 3 waren schon da. 2 Titel oder Notizen gekürzt (höchstens 200 bzw. 10.000 Zeichen).')
    expect(one('job-progress')).toBeNull()
  })

  it('TIME-24 EC-9 a job that failed says why and nothing came', async () => {
    await open(server({ more: (_method, path) => (path === `/api/imports/${PREVIEW.id}/apply` ? ok({ state: 'failed', done: 0, total: 212, problem: 'interrupted', result: null }) : undefined) }))
    await pick('familie-2025.ics')
    await click(one('load-apply'))
    expect(text('load-problem')).toBe('Der Server hat neu gestartet. Es wurde nichts übernommen; starte es noch einmal.')
    expect(one('load-result')).toBeNull()
  })

  it('TIME-24 EC-4 a Windows-1252 file says how it was read', async () => {
    await open(server({ preview: { ...PREVIEW, encoding: 'cp1252' } }))
    await pick('alt.ics')
    expect(text('load-encoding')).toBe('Die Datei war nicht in UTF-8 und wurde als Windows-1252 (Westeuropa) gelesen.')
  })

  it('TIME-24 EC-10 another calendar chosen is another move with a reference of its own', async () => {
    const movable = [{ id: 20, name: 'Persönlich', color: 'orange', kind: 'icloud' }, { id: 22, name: 'Arbeit', color: 'teal', kind: 'icloud' }]
    let fail = true
    await open(server({
      movable,
      more: (method, path) => {
        if (path === '/api/calendars/22/move' && method === 'GET') return ok({ calendar: { id: 22, name: 'Arbeit', color: 'teal' }, kind: 'icloud', events: 4, invitations: 0, moved_to: null, shared: false, state: 'ok', account_events: 10, limit: 20000, over_limit: false })
        if (path === '/api/calendars/20/move' && method === 'POST' && fail) return { status: 409, body: { detail: { code: 'move_source_unreachable' } } }
        if (path === '/api/calendars/22/move' && method === 'POST') return ok(done({ made: 4, existing: 0, failed: [], invitations: 0, again: false, kind: 'icloud', shared: false, from: { id: 22, name: 'Arbeit' }, calendar: { id: 31, name: 'Arbeit' } }))
        return undefined
      },
    }))
    await click(one('move-go'))
    fail = false
    const select = one('move-calendar') as HTMLSelectElement
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, '22')
    await act(async () => void select.dispatchEvent(new Event('change', { bubbles: true })))
    await settle()
    await click(one('move-go'))
    const posts = calls.filter((call) => call.method === 'POST' && call.path.endsWith('/move'))
    const refs = posts.map((call) => (call.body as { client_ref: string }).client_ref)
    expect(posts.map((call) => call.path)).toEqual(['/api/calendars/20/move', '/api/calendars/22/move'])
    expect(refs[0]).not.toBe(refs[1])
  })

  it('TIME-24 AC-3 TIME-28 AC-3 without a connection a file is not taken and nothing moves', async () => {
    await open()
    await pick('familie-2025.ics')
    await act(async () => markUnreachable())
    await settle()
    expect((one('load-apply') as HTMLButtonElement).disabled).toBe(true)
    expect((one('move-go') as HTMLButtonElement).disabled).toBe(true)
    expect((one('load-file') as HTMLInputElement).disabled).toBe(true)
  })
})
