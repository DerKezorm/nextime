/** TIME-9 in the interface: the attachments of an event with name, size, pages and preview; uploading with progress
 * and "Abbrechen", too large files refused before sending, broken uploads with "Erneut hochladen", a proxy that refuses;
 * × with a question and only with the right to change; the sentence about guests; events of iCloud with their own files;
 * saving while an upload runs, and closing without saving. Uploads go through a stand-in XMLHttpRequest. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { AttachmentInfo } from './features/attachments/api'
import { forgetUploads } from './features/attachments/uploads'
import type { EventDetail } from './features/calendars/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, choose, click, me, ok, render, serve, settle, signedIn, start, stop } from './testing'

const anna = me({ display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })
const MB = 1024 * 1024

class FakeXhr {
  static made: FakeXhr[] = []
  upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  ontimeout: (() => void) | null = null
  status = 0
  responseText = ''
  url = ''
  headers: Record<string, string> = {}
  body: unknown = null
  aborted = false

  constructor() {
    FakeXhr.made.push(this)
  }
  open(_method: string, url: string) {
    this.url = url
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value
  }
  send(body: unknown) {
    this.body = body
  }
  abort() {
    this.aborted = true
    this.onabort?.()
  }
  async progress(part: number) {
    await act(async () => this.upload.onprogress?.({ lengthComputable: true, loaded: part * 100, total: 100 }))
  }
  async answer(status: number, body: unknown) {
    this.status = status
    this.responseText = typeof body === 'string' ? body : JSON.stringify(body)
    await act(async () => this.onload?.())
    await settle()
  }
  async fail() {
    await act(async () => this.onerror?.())
    await settle()
  }
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-26T15:05:00Z'))
  start()
  FakeXhr.made = []
  vi.stubGlobal('XMLHttpRequest', FakeXhr)
  await i18n.changeLanguage('de')
})
afterEach(() => {
  forgetUploads()
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function info(change: Partial<AttachmentInfo> = {}): AttachmentInfo {
  return { id: 'a'.repeat(22), name: 'Einladung.pdf', kind: 'pdf', mime: 'application/pdf', size: 2_516_582, pages: 3, width: 200, height: 300, view: false, thumb: true, inline: true, created_at: '2026-11-26T15:00:00Z', ...change }
}

function withFiles(items: AttachmentInfo[], change: Partial<EventDetail> = {}): EventDetail {
  return eventDetail({ attachments: items, attachments_allowed: true, attachment_max_mb: 25, remote_attachments: [], ...change })
}

const lists = calendarList(
  [calendar(1, 'Persönlich', { default: true }), calendar(9, 'Arbeit', { group: 'icloud', origin: { id: 1, kind: 'icloud', label: 'anna@example.com', state: 'ok', synced_at: null } })],
  { attachment_max_mb: 25 },
)

function server(detail: EventDetail | null) {
  serve(
    signedIn(anna, (method, path) => {
      if (path.startsWith('/api/calendars')) return ok(lists)
      if (path.startsWith('/api/events?') && detail)
        return ok([{ id: detail.id, occurrence: null, calendar_id: detail.calendar_id, title: detail.title, busy: true, busy_only: detail.busy_only, recurring: false, all_day: false, start: '2026-11-26T17:30:00Z', end: '2026-11-26T19:00:00Z', time_zone: 'Europe/Berlin' }])
      if (detail && path.startsWith(`/api/events/${detail.id}`) && method === 'GET' && !path.includes('remote')) return ok(detail)
      if (path.startsWith('/api/attachments/') && method === 'DELETE') return ok({})
      if (path === '/api/events' && method === 'POST') return { status: 201, body: eventDetail() }
      return undefined
    }),
  )
}

async function open(): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    '/',
  )
}

const field = <T extends HTMLElement>(id: string) => document.querySelector<T>(`[data-testid="${id}"]`)

async function openEvent(): Promise<void> {
  await click(box.querySelector('[data-testid="next"], [data-testid="today-row"], [data-testid="next-day-row"]'))
}

function fileOf(name: string, size: number): File {
  const file = new File(['x'], name, { type: 'application/octet-stream' })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

async function pick(files: File[]): Promise<void> {
  const input = field<HTMLInputElement>('attachment-input')!
  Object.defineProperty(input, 'files', { configurable: true, value: files })
  await act(async () => void input.dispatchEvent(new Event('change', { bubbles: true })))
  await settle()
}

describe('TIME-9 the attachments of an event', () => {
  it('TIME-9 AC-1 each with preview, name, size and pages; PDFs and pictures open, other files download', async () => {
    server(withFiles([info(), info({ id: 'b'.repeat(22), name: 'Tickets.zip', kind: 'file', mime: 'application/octet-stream', size: 380 * 1024, pages: null, thumb: false, inline: false })]))
    await open()
    await openEvent()
    const items = [...document.querySelectorAll('[data-testid="attachment"]')]
    expect(items.map((item) => item.querySelector('[data-testid="attachment-name"]')!.textContent)).toEqual(['Einladung.pdf', 'Tickets.zip'])
    expect(items[0].textContent).toContain('2,4 MB · 3 Seiten')
    expect(items[1].textContent).toContain('380 KB')
    expect(items[0].querySelector('img')!.getAttribute('src')).toBe(`/api/attachments/${'a'.repeat(22)}/thumb`)
    const links = items.map((item) => item.querySelector('a')!)
    expect(links[0].getAttribute('href')).toBe(`/api/attachments/${'a'.repeat(22)}/file`)
    expect(links[0].getAttribute('target')).toBe('_blank')
    expect(links[1].getAttribute('href')).toBe(`/api/attachments/${'b'.repeat(22)}/file?download=1`)
  })

  it('TIME-9 EC-4 AC-8 without attachments only the surface to drop and the sentence about guests', async () => {
    server(withFiles([]))
    await open()
    await openEvent()
    expect(field('attachment-list')).toBeNull()
    expect(field('attachment-drop')!.textContent).toBe('Dateien hierher ziehen oder auswählen')
    expect(field('attachment-guests')!.textContent).toBe('Wer den Kalender sehen darf, sieht auch die Anhänge. Gäste über den Lese-Link nicht.')
  })

  it('TIME-9 AC-7 AC-6 several files upload each with its own progress, and "Abbrechen" leaves nothing', async () => {
    server(withFiles([]))
    await open()
    await openEvent()
    await pick([fileOf('Saalplan.png', 2 * MB), fileOf('Einladung.pdf', MB)])
    expect(FakeXhr.made).toHaveLength(2)
    expect(FakeXhr.made[0].url).toMatch(/^\/api\/attachments\?id=[A-Za-z0-9_-]{22}&name=Saalplan.png&event_id=7$/)
    expect(FakeXhr.made[0].headers['X-Nextime-Client']).toBeTruthy()
    await FakeXhr.made[0].progress(0.5)
    await FakeXhr.made[1].progress(0.25)
    expect([...document.querySelectorAll('[data-testid="upload-percent"]')].map((each) => each.textContent)).toEqual(['50 %', '25 %'])
    await click(document.querySelectorAll('[data-testid="upload-cancel"]')[1])
    expect(FakeXhr.made[1].aborted).toBe(true)
    expect(document.querySelectorAll('[data-testid="upload"]')).toHaveLength(1)
    await FakeXhr.made[0].answer(201, info({ id: 'c'.repeat(22), name: 'Saalplan.png', kind: 'image', mime: 'image/png', pages: null }))
    expect(document.querySelectorAll('[data-testid="upload"]')).toHaveLength(0)
    expect([...document.querySelectorAll('[data-testid="attachment-name"]')].map((each) => each.textContent)).toEqual(['Saalplan.png'])
  })

  it('TIME-9 AC-2 EC-5 a file over the limit is refused before it is sent, with name, size and limit, and "Ausblenden"', async () => {
    server(withFiles([]))
    await open()
    await openEvent()
    await pick([fileOf('Probe.mov', 140 * MB), fileOf('Foto.png', MB)])
    expect(FakeXhr.made).toHaveLength(1)
    expect(field('upload-too-large')!.textContent).toContain('Probe.mov ist zu groß (140 MB, höchstens 25 MB).')
    await click(field('upload-hide'))
    expect(field('upload-too-large')).toBeNull()
  })

  it('TIME-9 AC-2 a refusal of the server names the limit too', async () => {
    server(withFiles([]))
    await open()
    await openEvent()
    await pick([fileOf('genau.bin', 24 * MB)])
    await FakeXhr.made[0].answer(413, { detail: { code: 'attachment_too_large', message: 'x', name: 'genau.bin', size: 26 * MB, max_mb: 10 } })
    expect(field('upload-too-large')!.textContent).toContain('genau.bin ist zu groß (24 MB, höchstens 10 MB).')
  })

  it('TIME-9 EC-11 a broken upload says the file is missing and may go again', async () => {
    server(withFiles([]))
    await open()
    await openEvent()
    await pick([fileOf('Ticket.pdf', MB)])
    await FakeXhr.made[0].fail()
    expect(field('upload-failed')!.textContent).toContain('Ticket.pdf: Das Hochladen brach ab. Die Datei fehlt noch.')
    await click(field('upload-retry'))
    expect(FakeXhr.made).toHaveLength(2)
  })

  it('TIME-9 EC-12 a reverse proxy refusing large files and a full disk are said plainly', async () => {
    server(withFiles([]))
    await open()
    await openEvent()
    await pick([fileOf('Video.mp4', 5 * MB), fileOf('Ticket.pdf', MB)])
    await FakeXhr.made[0].answer(413, '<html><body>413 Request Entity Too Large</body></html>')
    await FakeXhr.made[1].answer(507, { detail: { code: 'storage_failed', message: 'x' } })
    const failed = [...document.querySelectorAll('[data-testid="upload-failed"]')].map((each) => each.textContent)
    expect(failed[0]).toContain('Video.mp4: Der Server nimmt so große Dateien nicht an.')
    expect(failed[1]).toContain('Ticket.pdf: Der Server konnte die Datei nicht speichern')
  })

  it('TIME-9 AC-9 × asks and removes for good; with the right to read there is no ×', async () => {
    server(withFiles([info()]))
    await open()
    await openEvent()
    await click(field('attachment-remove'))
    expect(field('attachment-remove-question')!.textContent).toContain('„Einladung.pdf“ ist danach endgültig weg, nicht im Papierkorb.')
    await click(field('attachment-remove-yes'))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === `/api/attachments/${'a'.repeat(22)}`)).toBe(true)
    expect(field('attachment')).toBeNull()
  })

  it('TIME-9 AC-3 AC-9 with the right to read the attachments show, without × and without the surface', async () => {
    server(withFiles([info()], { writable: false, right: 'read', attachments_allowed: false, shared: true }))
    await open()
    await openEvent()
    expect(field('read-only')).not.toBeNull()
    expect(field('attachment-name')!.textContent).toBe('Einladung.pdf')
    expect(field('attachment-remove')).toBeNull()
    expect(field('attachment-drop')).toBeNull()
  })

  it('TIME-9 EC-6 EC-8 a name with markup and a long one stay text, cut in the list and whole in the title', async () => {
    const long = 'Übersicht 🎟️ ' + 'Ä'.repeat(190) + '.pdf'
    server(withFiles([info({ name: '<img src=x onerror=alert(1)>.pdf', thumb: false }), info({ id: 'b'.repeat(22), name: long })]))
    await open()
    await openEvent()
    const names = [...document.querySelectorAll('[data-testid="attachment-name"]')]
    expect(names[0].textContent).toBe('<img src=x onerror=alert(1)>.pdf')
    expect(names[0].querySelector('img')).toBeNull()
    expect(names[1].getAttribute('title')).toBe(long)
    expect(names[1].className).toContain('truncate')
  })

  it('TIME-9 AC-5 EC-14 an event of iCloud shows its files, takes none, and says when one cannot be had', async () => {
    server(withFiles([], { calendar_id: 9, source: 'icloud', attachments_allowed: false, remote_attachments: [{ index: 0, name: 'Protokoll.pdf', mime: 'application/pdf', size: 17, link: 'https://p01-caldav.icloud.com/a/1' }] }))
    await open()
    await openEvent()
    expect(field('attachments-own-only')!.textContent).toBe('Anhänge gibt es nur an Terminen in nextime-Kalendern.')
    expect(field('attachment-drop')).toBeNull()
    expect(field('remote-attachment')!.textContent).toBe('Protokoll.pdf')
    serve(signedIn(anna, (_method, path) => (path.includes('/remote-attachments/0') ? { status: 502, body: { detail: { code: 'remote_unavailable' } } } : undefined)))
    await click(field('remote-attachment'))
    expect(field('remote-missing')!.textContent).toBe('„Protokoll.pdf“ ist gerade nicht abrufbar. Es liegt weiter in iCloud.')
  })

  it('TIME-9 AC-5 moving an event with attachments to iCloud warns first', async () => {
    server(withFiles([info()]))
    await open()
    await openEvent()
    expect(field('attachments-move-loses')).toBeNull()
    await choose(field<HTMLSelectElement>('field-calendar')!, '9')
    expect(field('attachments-move-loses')!.textContent).toBe('Beim Wechsel nach iCloud geht der Anhang verloren; iCloud kann keine tragen.')
  })
})

describe('TIME-9 a new event', () => {
  async function newEvent(): Promise<void> {
    await click(box.querySelector('[data-testid="new-event"]'))
    const title = field<HTMLInputElement>('field-title')!
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(title, 'Konzert')
    act(() => void title.dispatchEvent(new Event('input', { bubbles: true })))
  }

  it('TIME-9 AC-10 saving does not wait for an upload, which goes on and lands at the saved event', async () => {
    server(null)
    await open()
    await newEvent()
    await pick([fileOf('Tickets.pdf', 3 * MB)])
    const ref = new URL('http://x' + FakeXhr.made[0].url).searchParams.get('ref')
    expect(ref).toBeTruthy()
    await click(field('panel-save'))
    const made = calls.find((call) => call.method === 'POST' && call.path === '/api/events')!.body as { client_ref: string }
    expect(made.client_ref).toBe(ref)
    expect(field('event-panel')).toBeNull()
    expect(FakeXhr.made[0].aborted).toBe(false)
  })

  it('TIME-9 AC-10 closing without saving while an upload runs asks, and then stops it', async () => {
    server(null)
    await open()
    await newEvent()
    await pick([fileOf('Tickets.pdf', 3 * MB)])
    await click(field('panel-close'))
    expect(field('uploads-question')!.textContent).toContain('Schließt du den Termin ohne Sichern, fehlen die Dateien, die noch kommen.')
    await click(byText('Offen lassen'))
    expect(field('event-panel')).not.toBeNull()
    await click(field('panel-close'))
    await click(field('uploads-close'))
    expect(FakeXhr.made[0].aborted).toBe(true)
    expect(field('event-panel')).toBeNull()
  })

  it('TIME-9 AC-10 discarding a new event takes its finished files away', async () => {
    server(null)
    await open()
    await newEvent()
    await pick([fileOf('Tickets.pdf', MB)])
    await FakeXhr.made[0].answer(201, info({ id: 'd'.repeat(22), name: 'Tickets.pdf' }))
    await click(field('panel-close'))
    await click(byText('Verwerfen'))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === `/api/attachments/${'d'.repeat(22)}`)).toBe(true)
  })
})
