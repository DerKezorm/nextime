/** TIME-20 in the interface: "Auf iPhone und Co" (name a device, QR code, the data to copy, the password only now),
 * "Deine Geräte" with locking out, the hint without HTTPS, and the setup on the phone (profile, Android, used and
 * expired links). */
import { act } from 'react'

import App from './App'
import { browser, type DeviceOverview, type NewDevice } from './features/calendars/api'
import { LiveRefresh, useRevision } from './features/calendars/store'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, ok, render, serve, signedIn, start, stop, type } from './testing'

const anna = me({ name: 'anna', display_name: 'Anna', display: { week_start: 1, week_numbers: true, clock: '24', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })
const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1'
const ANDROID = 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36'
const DESKTOP = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-11-26T15:05:00Z'))
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function agent(text: string): void {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(text)
}

const calendars = [
  { id: 1, name: 'Persönlich', color: 'slate' as const, right: 'owner' as const },
  { id: 2, name: 'Familie', color: 'green' as const, right: 'edit' as const },
  { id: 3, name: 'Arbeit Anna', color: 'gray' as const, right: 'read' as const },
  { id: 4, name: 'Annas iCloud', color: 'teal' as const, right: 'busy' as const },
]

function overview(change: Partial<DeviceOverview> = {}): DeviceOverview {
  return { devices: [], https: true, address: 'https://nextime.example.com/dav/', user: 'anna', calendars, minutes: 10, ...change }
}

const made: NewDevice = {
  device: { id: 5, name: 'iPhone von Anna', client: '', created_at: '2026-11-26T15:05:00Z', last_used_at: null },
  password: 'probe-password-1',
  address: 'https://nextime.example.com/dav/',
  user: 'anna',
  code: 'setup-code',
  link: 'https://nextime.example.com/setup/setup-code',
  qr: 'data:image/svg+xml,%3Csvg%3E%3C/svg%3E',
  minutes: 10,
  devices: [{ id: 5, name: 'iPhone von Anna', client: '', created_at: '2026-11-26T15:05:00Z', last_used_at: null }],
}

function server(list: DeviceOverview, account = anna) {
  serve(
    signedIn(account, (method, path) => {
      if (path === '/api/devices' && method === 'GET') return ok(list)
      if (path === '/api/devices' && method === 'POST') return { status: 201, body: made }
      if (path.startsWith('/api/devices/') && method === 'DELETE') return { status: 204, body: null }
      return undefined
    }),
  )
}

async function open(at: string): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
}

describe('TIME-20 on iPhone and co', () => {
  it('TIME-20 AC-1 AC-7 AC-8 naming a device shows the QR code, the steps and the data to copy, the password only now', async () => {
    agent(DESKTOP)
    server(overview())
    await open('/settings/devices')
    expect(box.textContent).toContain('Deine eigenen und mit dir geteilten Kalender erscheinen in Apple Kalender, auf Android und in anderen Programmen.')
    type(box.querySelector<HTMLInputElement>('[data-testid="device-name"]')!, 'iPhone von Anna')
    await click(box.querySelector('[data-testid="device-create"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/devices')?.body).toEqual({ name: 'iPhone von Anna' })
    const shown = box.querySelector<HTMLElement>('[data-testid="device-made"]')!
    expect(shown.querySelector('img')?.getAttribute('alt')).toBe('QR-Code zur Einrichtung')
    expect([...shown.querySelectorAll('ol li')].map((item) => item.textContent)).toEqual(['Mit der Kamera des iPhones scannen', '„Profil laden“ antippen', 'In den Einstellungen unter „Profil geladen“ installieren'])
    expect(shown.textContent).toContain('Der Code gilt 10 Minuten.')
    expect(shown.querySelector('[data-testid="data-address"]')?.textContent).toBe('https://nextime.example.com/dav/')
    expect(shown.querySelector('[data-testid="data-user"]')?.textContent).toBe('anna')
    expect(shown.querySelector('[data-testid="data-password"]')?.textContent).toBe('probe-password-1')
    expect(shown.querySelector('[data-testid="only-now"]')?.textContent).toBe('Das Gerätepasswort ist nur jetzt sichtbar. Dein Anmeldepasswort brauchst du dort nicht.')
    expect([...shown.querySelectorAll('button')].filter((button) => button.textContent === 'Kopieren')).toHaveLength(3)
  })

  it('TIME-20 EC-9 an empty name asks for one, and names with markup stay text', async () => {
    agent(DESKTOP)
    server(overview({ devices: [{ id: 1, name: '<b>Ölgerät</b> 📱', client: '', created_at: '2026-11-26T15:00:00Z', last_used_at: null }] }))
    await open('/settings/devices')
    type(box.querySelector<HTMLInputElement>('[data-testid="device-name"]')!, '   ')
    await click(box.querySelector('[data-testid="device-create"]'))
    expect(box.textContent).toContain('Bitte gib dem Gerät einen Namen.')
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/devices')).toBe(false)
    expect(box.querySelector('[data-testid="device-list"] b')).toBeNull()
    expect(box.querySelector('[data-testid="device-list"]')?.textContent).toContain('<b>Ölgerät</b> 📱')
  })

  it('TIME-20 AC-5 EC-7 the list names program and last sync, an unfinished setup none, and locks out after asking', async () => {
    agent(DESKTOP)
    server(overview({ devices: [
      { id: 1, name: 'iPhone', client: 'Apple Calendar', created_at: '2026-11-20T10:00:00Z', last_used_at: '2026-11-26T15:02:00Z' },
      { id: 2, name: 'Altes Handy', client: '', created_at: '2026-11-26T15:00:00Z', last_used_at: null },
    ] }))
    await open('/settings/devices')
    const lines = [...box.querySelectorAll('[data-testid="device-last"]')].map((line) => line.textContent)
    expect(lines).toEqual(['Apple Calendar · zuletzt vor 3 Minuten', 'noch nie abgeglichen'])
    expect(box.querySelector('[data-testid="device-list"]')?.textContent).toContain('Ein ausgesperrtes Gerät bekommt sofort keine Termine mehr. Die anderen laufen weiter.')
    await click(box.querySelectorAll('[data-testid="lock-out"]')[1])
    expect(document.querySelector('[role="dialog"]')?.getAttribute('aria-label')).toBe('„Altes Handy“ aussperren?')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(byText('Aussperren', document.querySelector<HTMLElement>('[data-testid="lock-out-question"]')!))
    expect(calls.find((call) => call.method === 'DELETE')?.path).toBe('/api/devices/2')
  })

  it('TIME-20 EC-8 without devices an empty state says how to set one up', async () => {
    agent(DESKTOP)
    server(overview())
    await open('/settings/devices')
    expect(box.querySelector('[data-testid="devices-empty"]')?.textContent).toBe('Noch kein Gerät eingerichtet. Gib oben einem Gerät einen Namen, dann bekommst du QR-Code und Passwort.')
  })

  it('TIME-20 EC-21 without HTTPS a hint takes the place of the setup', async () => {
    agent(DESKTOP)
    server(overview({ https: false }), { ...anna, role: 'operator' })
    await open('/settings/devices')
    const hint = box.querySelector('[data-testid="https-hint"]')!
    expect(hint.textContent).toContain('Dafür braucht nextime HTTPS.')
    expect(hint.textContent).toContain('Das richtet der Betreiber deines nextime-Servers ein.')
    expect(hint.textContent).toContain('Du bist der Betreiber')
    expect(box.querySelector('[data-testid="device-create"]')).toBeNull()
  })
})

describe('TIME-20 on the phone', () => {
  function setupServer(answer: { status: number; body: unknown }) {
    serve((method, path) => {
      if (path === '/api/device-setup/setup-code' && method === 'GET') return answer
      if (path === '/api/device-setup/setup-code/credentials') return ok({ address: 'https://nextime.example.com/dav/', user: 'anna', password: 'probe-password-1', device: 'Pixel' })
      if (path === '/api/setup') return ok({ needs_setup: false, code_required: true, signed_in: false, version: '0.1.0', min_password: 12 })
      return undefined
    })
  }

  it('TIME-20 AC-9 AC-2 the iPhone page says what comes, lists the calendars with their right and loads the profile', async () => {
    agent(IPHONE)
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined)
    setupServer(ok({ device: 'iPhone von Anna', account: 'Anna', calendars, expires_at: '2026-11-26T15:15:00Z' }))
    await open('/setup/setup-code')
    const page = document.querySelector<HTMLElement>('[data-testid="phone-setup"]')!
    expect(page.querySelector('h2')?.textContent).toBe('Auf diesem iPhone einrichten')
    expect(page.textContent).toContain('Deine Kalender erscheinen danach in Apple Kalender, mit Erinnerungen und Widgets. Eintragen geht dort auch.')
    expect(page.querySelector<HTMLInputElement>('[data-testid="phone-name"]')?.value).toBe('iPhone von Anna')
    expect([...page.querySelectorAll('ol li')].map((item) => item.textContent)).toEqual(['Einstellungen öffnen', 'Oben „Profil geladen“ antippen', '„Installieren“ und mit Code bestätigen'])
    const along = [...page.querySelectorAll('[data-testid="coming-along"] li')].map((item) => item.textContent)
    expect(along).toEqual(['Persönlichbearbeiten', 'Familiebearbeiten', 'Arbeit Annalesen', 'Annas iCloudnur belegt'])
    expect(page.textContent).toContain('iCloud und Google hat dein iPhone schon selbst, die kommen nicht doppelt.')
    type(page.querySelector<HTMLInputElement>('[data-testid="phone-name"]')!, 'Mein iPhone')
    await click(page.querySelector('[data-testid="load-profile"]'))
    expect(go).toHaveBeenCalledWith('/api/device-setup/setup-code/profile?name=Mein%20iPhone')
  })

  it('TIME-20 EC-23 on Android the page shows the data to copy instead of a profile', async () => {
    agent(ANDROID)
    setupServer(ok({ device: 'iPhone von Anna', account: 'Anna', calendars, expires_at: '2026-11-26T15:15:00Z' }))
    await open('/setup/setup-code')
    expect(document.querySelector('[data-testid="load-profile"]')).toBeNull()
    type(document.querySelector<HTMLInputElement>('[data-testid="phone-name"]')!, 'Pixel')
    await click(document.querySelector('[data-testid="phone-show-data"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path.endsWith('/credentials'))?.body).toEqual({ name: 'Pixel' })
    const data = document.querySelector('[data-testid="phone-data"]')!
    expect(data.querySelector('[data-testid="data-password"]')?.textContent).toBe('probe-password-1')
    expect(data.textContent).toContain('Das Gerätepasswort ist nur jetzt sichtbar.')
  })

  it('TIME-20 AC-13 a link used once says so', async () => {
    agent(IPHONE)
    setupServer({ status: 410, body: { detail: { code: 'setup_used', message: 'x' } } })
    await open('/setup/setup-code')
    expect(document.querySelector('[data-testid="setup-refused"]')?.textContent).toBe('Dieser Code wurde schon benutzt. Erzeuge am Rechner unter „Auf iPhone und Co“ einen neuen.')
    expect(document.querySelector('[data-testid="load-profile"]')).toBeNull()
  })

  it('TIME-20 EC-6 a link older than ten minutes says it expired', async () => {
    agent(IPHONE)
    setupServer({ status: 410, body: { detail: { code: 'setup_expired', message: 'x' } } })
    await open('/setup/setup-code')
    expect(document.querySelector('[data-testid="setup-refused"]')?.textContent).toBe('Dieser Code ist abgelaufen. Erzeuge am Rechner unter „Auf iPhone und Co“ einen neuen.')
  })

  it('TIME-20 AC-2 signed in on the iPhone, the tab is the setup on this phone', async () => {
    agent(IPHONE)
    const go = vi.spyOn(browser, 'go').mockImplementation(() => undefined)
    server(overview())
    await open('/settings/devices')
    const page = document.querySelector<HTMLElement>('[data-testid="phone-setup"]')!
    expect(page.querySelector<HTMLInputElement>('[data-testid="phone-name"]')?.value).toBe('iPhone von Anna')
    await click(page.querySelector('[data-testid="load-profile"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/devices')?.body).toEqual({ name: 'iPhone von Anna' })
    expect(go).toHaveBeenCalledWith('/api/device-setup/setup-code/profile?name=iPhone%20von%20Anna')
  })
})

describe('TIME-20 changes show at once', () => {
  /** A stand-in for the browser's EventSource: the test opens, fails or speaks for the server. */
  class FakeSource {
    static last: FakeSource | null = null
    url: string
    onopen: (() => void) | null = null
    onerror: (() => void) | null = null
    closed = false
    private handlers: Record<string, (() => void)[]> = {}
    constructor(url: string) {
      this.url = url
      FakeSource.last = this
    }
    addEventListener(name: string, handler: () => void) {
      ;(this.handlers[name] ??= []).push(handler)
    }
    emit(name: string) {
      for (const handler of this.handlers[name] ?? []) handler()
    }
    close() {
      this.closed = true
    }
  }

  function Probe() {
    const seen = useRevision()
    return <span data-testid="revision">{seen}</span>
  }

  const revision = () => Number(box.querySelector('[data-testid="revision"]')?.textContent)

  it('TIME-20 AC-4 the open page listens and asks again as soon as a change is told', async () => {
    vi.stubGlobal('EventSource', FakeSource)
    await render(
      <>
        <LiveRefresh />
        <Probe />
      </>,
    )
    expect(FakeSource.last?.url).toBe('/api/live')
    const before = revision()
    await act(async () => FakeSource.last!.emit('changed'))
    expect(revision()).toBe(before + 1)
  })

  it('TIME-20 AC-4 while the stream is down the page asks every few seconds, and stops once it is back', async () => {
    vi.stubGlobal('EventSource', FakeSource)
    vi.useRealTimers()
    await render(
      <>
        <LiveRefresh every={20} />
        <Probe />
      </>,
    )
    const source = FakeSource.last!
    const before = revision()
    await act(async () => source.onerror?.())
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 90)))
    const polled = revision()
    expect(polled).toBeGreaterThan(before + 1)
    await act(async () => source.onopen?.())
    const reopened = revision()
    await act(async () => await new Promise((resolve) => setTimeout(resolve, 90)))
    expect(revision()).toBe(reopened)
  })
})
