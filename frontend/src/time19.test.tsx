/** TIME-19 in the interface: the dialog "Teilen …" for own calendars (people with their right, the guest link) and for
 * calendars from iCloud and Google (whole or busy, the preview), "Freigaben" under Settings, the guest page, and the
 * entries of the calendar menu. */
import App from './App'
import { calendar, calendarList } from './calendarFakes'
import type { CalendarEntry } from './features/calendars/api'
import type { GuestPage as Page, Sharing, SharesOverview } from './features/shares/api'
import { GuestPage } from './features/shares/GuestPage'
import { ShareDialog } from './features/shares/ShareDialog'
import { SharesArea } from './features/shares/SharesArea'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, byText, calls, choose, click, me, ok, render, serve, settle, signedIn, start, stop, type } from './testing'
import { Route, Routes } from 'react-router-dom'

const tester = me({ id: 1, name: 'tester', display_name: 'Tester', role: 'operator' })

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
  document.documentElement.removeAttribute('data-theme')
})

const anna = { id: 2, name: 'Anna', email: 'anna@example.com', color: 3, avatar: null }
const ben = { id: 3, name: 'Ben', email: 'ben@example.com', color: 4, avatar: null }

function sharing(change: Partial<Sharing> = {}): Sharing {
  return {
    calendar: { id: 5, name: 'Familie', color: 'green', kind: 'own' },
    rights: ['read', 'edit'],
    people: [{ ...anna, right: 'edit', since: '2026-11-20T10:00:00Z' }],
    links_allowed: true,
    link: null,
    ...change,
  }
}

const LINK = { url: 'https://kalender.example.com/abo/q8Zt3f', feed: 'https://kalender.example.com/abo/q8Zt3f.ics', since: '2026-11-26T15:00:00Z', expires_on: null }

/** A server that keeps the sharing as the requests change it. */
function sharingServer(first: Sharing, people: (typeof anna)[] = [anna, ben], account = member) {
  let state = first
  serve(signedIn(account, (method, path, body) => {
    if (path.startsWith('/api/shares/people')) {
      const query = (new URL(path, 'http://x').searchParams.get('q') ?? '').toLowerCase()
      return ok({ people: people.filter((person) => person.name.toLowerCase().includes(query) || person.email.includes(query)) })
    }
    const person = path.match(/sharing\/(\d+)$/)
    if (person && method === 'PUT') {
      const id = Number(person[1])
      const found = people.find((each) => each.id === id)!
      const right = (body as { right: 'read' | 'edit' | 'busy' }).right
      const rest = state.people.filter((each) => each.id !== id)
      state = { ...state, people: [...rest, { ...found, right, since: '2026-11-26T15:05:00Z' }] }
      return ok(state)
    }
    if (person && method === 'DELETE') {
      state = { ...state, people: state.people.filter((each) => each.id !== Number(person[1])) }
      return ok(state)
    }
    if (path.endsWith('/guest-link') && method === 'PUT') {
      state = { ...state, link: { ...LINK, expires_on: (body as { expires_on: string | null }).expires_on } }
      return ok(state)
    }
    if (path.endsWith('/guest-link') && method === 'DELETE') {
      state = { ...state, link: null }
      return ok(state)
    }
    if (path.endsWith('/sharing')) return ok(state)
    return undefined
  }))
}

const member = me({ id: 1, name: 'tester', display_name: 'Tester', role: 'member' })

async function dialog(first: Sharing, people?: (typeof anna)[], account = member, onClose: () => void = () => undefined) {
  sharingServer(first, people, account)
  await render(
    <AuthProvider>
      <ShareDialog calendarId={first.calendar.id} name={first.calendar.name} color={first.calendar.color} onClose={onClose} />
    </AuthProvider>,
  )
}

const field = () => box.querySelector<HTMLInputElement>('[data-testid="share-search"]')!

async function search(text: string) {
  vi.useRealTimers()
  type(field(), text)
  await new Promise((resolve) => setTimeout(resolve, 260))
  await settle()
}

describe('TIME-19 the dialog for an own calendar', () => {
  it('TIME-19 AC-9 says only the owner decides, lists picture, name, address, right and ×, and "Fertig" closes', async () => {
    await dialog(sharing())
    const root = box.querySelector('[data-testid="share-dialog"]')!
    expect(document.querySelector('[role="dialog"]')!.getAttribute('aria-label')).toBe('„Familie“ teilen')
    expect(root.querySelector('[data-testid="share-hint"]')!.textContent).toBe('Nur du entscheidest, wer diesen Kalender sieht. Weiterteilen kann nur du.')
    const row = root.querySelector('[data-testid="share-person"]')!
    expect(row.querySelector('[data-testid="avatar-initial"]')!.textContent).toBe('A')
    expect(row.textContent).toContain('Anna')
    expect(row.textContent).toContain('anna@example.com')
    const right = row.querySelector('select')!
    expect(right.value).toBe('edit')
    expect([...right.options].map((option) => option.textContent)).toEqual(['bearbeiten', 'lesen'])
    expect(row.querySelector('button[aria-label="Anna entfernen"]')).not.toBeNull()
    expect(byText('Fertig', root)).toBeDefined()
  })

  it('TIME-19 AC-1 finds members by name or address and gives each a right', async () => {
    await dialog(sharing({ people: [] }))
    await search('be')
    const found = [...box.querySelectorAll('[data-testid="share-found"]')]
    expect(found.map((item) => item.textContent)).toEqual(['BBenben@example.com'])
    await click(found[0])
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/calendars/5/sharing/3')?.body).toEqual({ right: 'read' })
    const row = box.querySelector('[data-testid="share-person"]')!
    expect(row.textContent).toContain('Ben')
    await choose(row.querySelector('select')!, 'edit')
    expect(calls.filter((call) => call.method === 'PUT' && call.path === '/api/calendars/5/sharing/3').at(-1)?.body).toEqual({ right: 'edit' })
    await click(row.querySelector('button[aria-label="Ben entfernen"]'))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/calendars/5/sharing/3')).toBe(true)
    expect(box.querySelector('[data-testid="share-person"]')).toBeNull()
  })

  it('TIME-19 EC-8 two of the same name are told apart by their address, whatever the case typed', async () => {
    const one = { ...anna, id: 7, name: 'Anna Berg', email: 'anna@familie.example.com' }
    const two = { ...anna, id: 8, name: 'Anna Berg', email: 'anna@verein.example.com' }
    await dialog(sharing({ people: [] }), [one, two])
    await search('ANNA BERG')
    const found = [...box.querySelectorAll('[data-testid="share-found"]')].map((item) => item.textContent)
    expect(found).toEqual(['AAnna Berganna@familie.example.com', 'AAnna Berganna@verein.example.com'])
  })

  it('TIME-19 EC-5 a search without hits says so and the guest link stays possible', async () => {
    await dialog(sharing({ people: [] }), [])
    await search('zz')
    expect(box.querySelector('[data-testid="share-nobody"]')!.textContent).toContain('Niemand auf diesem Server passt dazu')
    expect(box.querySelector('[data-testid="share-link-switch"]')).not.toBeNull()
    // A member is not offered to invite anybody.
    expect(box.querySelector('[data-testid="share-invite"]')).toBeNull()
  })

  it('TIME-19 EC-5 the operator is offered "Person einladen", as in nexnotes', async () => {
    await dialog(sharing({ people: [] }), [], me({ id: 1, name: 'tester', display_name: 'Tester', role: 'operator' }))
    await search('zz')
    const invite = box.querySelector<HTMLAnchorElement>('[data-testid="share-invite"]')!
    expect(invite.textContent).toBe('Person einladen')
    expect(invite.getAttribute('href')).toBe('/settings/accounts?invite=1')
  })

  it('TIME-19 AC-9 "Fertig" with a name typed but nobody chosen says so once, then closes', async () => {
    const closed = vi.fn()
    await dialog(sharing({ people: [] }), undefined, member, closed)
    type(field(), 'Ann')
    await click(box.querySelector('[data-testid="share-done"]'))
    expect(closed).not.toHaveBeenCalled()
    expect(box.querySelector('[data-testid="share-none-chosen"]')!.textContent).toBe('Noch niemand ausgewählt.')
    await click(box.querySelector('[data-testid="share-done"]'))
    expect(closed).toHaveBeenCalledTimes(1)
  })

  it('TIME-19 AC-5 switches the guest link on with an address to copy, "Gilt" until revoked or a day, and revokes it', async () => {
    await dialog(sharing())
    const part = () => box.querySelector('[data-testid="share-link"]')!
    expect(part().textContent).toContain('Link zum Lesen für Gäste ohne Konto')
    expect(box.querySelector('[data-testid="share-link-text"]')!.textContent).toBe(
      'Gäste sehen Termine, Zeiten und Orte, aber keine Anhänge. Im Kalender abonnieren oder im Browser ansehen.',
    )
    await click(box.querySelector('[data-testid="share-link-switch"]'))
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/calendars/5/guest-link')?.body).toEqual({ expires_on: null })
    expect(box.querySelector<HTMLInputElement>('[data-testid="share-link-url"]')!.value).toBe(LINK.url)
    const valid = box.querySelector<HTMLSelectElement>('[data-testid="share-link-valid"]')!
    expect(valid.selectedOptions[0].textContent).toBe('bis zum Widerruf')
    const copied = vi.fn(async () => undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: copied } })
    vi.stubGlobal('isSecureContext', true)
    await click(box.querySelector('[data-testid="share-link-copy"]'))
    expect(copied).toHaveBeenCalledWith(LINK.url)
    await choose(valid, 'until')
    const day = box.querySelector<HTMLInputElement>('[data-testid="share-link-day"]')!
    expect(day.value).toBe('2026-12-26')
    await click(box.querySelector('[data-testid="share-link-revoke"]'))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/calendars/5/guest-link')).toBe(true)
    expect(box.querySelector('[data-testid="share-link-url"]')).toBeNull()
  })

  it('TIME-19 EC-19 the last day cannot lie in the past', async () => {
    await dialog(sharing({ link: { ...LINK, expires_on: '2026-12-31' } }))
    const day = box.querySelector<HTMLInputElement>('[data-testid="share-link-day"]')!
    expect(day.min).toBe('2026-11-26')
    expect(day.required).toBe(true)
  })

  it('TIME-19 AC-7 without the operator\'s guest links the switch is missing', async () => {
    await dialog(sharing({ links_allowed: false }))
    expect(box.querySelector('[data-testid="share-link-switch"]')).toBeNull()
    expect(box.querySelector('[data-testid="share-link-off"]')!.textContent).toBe('Gast-Links hat der Betreiber ausgeschaltet.')
  })

  it('TIME-19 EC-15 a double click on the switch makes one request, and what stands is the server\'s answer', async () => {
    await dialog(sharing())
    const toggle = box.querySelector<HTMLButtonElement>('[data-testid="share-link-switch"]')!
    toggle.click()
    toggle.click()
    await settle()
    expect(calls.filter((call) => call.method === 'PUT' && call.path === '/api/calendars/5/guest-link')).toHaveLength(1)
    expect(toggle.getAttribute('aria-checked')).toBe('true')
  })

  it('TIME-19 EC-10 names and addresses with code stay plain text', async () => {
    const odd = { ...anna, name: '<img src=x onerror=alert(1)>' }
    await dialog(sharing({ calendar: { id: 5, name: '<b>Familie</b>', color: 'green', kind: 'own' }, people: [{ ...odd, right: 'read', since: '' }] }), [odd])
    expect(box.querySelector('img')).toBeNull()
    expect(box.querySelector('b')).toBeNull()
    expect(box.querySelector('[data-testid="share-person"]')!.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})

describe('TIME-19 the dialog for a calendar from iCloud or Google', () => {
  const icloud = () => sharing({ calendar: { id: 9, name: 'Arbeit', color: 'blue', kind: 'icloud' }, rights: ['read', 'busy'], people: [], link: null, links_allowed: false })

  it('TIME-19 AC-10 shows the origin, that no access is needed, "ganz zeigen | nur belegt" with a preview, "Abbrechen" and "Teilen"', async () => {
    await dialog(icloud())
    expect(box.querySelector('[data-testid="share-origin"]')!.textContent).toBe('aus iCloud')
    expect(box.querySelector('[data-testid="share-source-hint"]')!.textContent).toContain('keinen Zugang zu deinem iCloud-Konto')
    expect(box.textContent).toContain('Bei „nur belegt“ bleiben Titel, Ort und Notizen bei dir, auch in Suche und Mitteilungen.')
    await search('ann')
    await click(box.querySelector('[data-testid="share-found"]'))
    const preview = box.querySelector('[data-testid="share-preview"]')!
    expect(preview.textContent).toContain('Du siehst')
    expect(preview.textContent).toContain('Anna sieht')
    expect(preview.textContent).toContain('Belegt')
    await click(box.querySelector('[data-testid="share-seg-read"]'))
    expect(box.querySelector('[data-testid="share-preview"]')!.textContent).not.toContain('Belegt')
    await click(box.querySelector('[data-testid="share-seg-busy"]'))
    expect(byText('Abbrechen', box)).toBeDefined()
    await click(box.querySelector('[data-testid="share-submit"]'))
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/calendars/9/sharing/2')?.body).toEqual({ right: 'busy' })
  })

  it('TIME-19 AC-12 has no guest link', async () => {
    await dialog(icloud())
    expect(box.querySelector('[data-testid="share-link"]')).toBeNull()
    expect(box.querySelector('[data-testid="share-link-switch"]')).toBeNull()
    expect(box.querySelector('[data-testid="share-link-off"]')).toBeNull()
  })

  it('TIME-19 AC-10 changes wait for "Teilen"; "Abbrechen" drops them', async () => {
    const shared = () => icloud()
    const withAnna = { ...shared(), people: [{ ...anna, right: 'busy' as const, since: '' }, { ...ben, right: 'read' as const, since: '' }] }
    const closed = vi.fn()
    await dialog(withAnna, undefined, member, closed)
    const rows = () => [...box.querySelectorAll('[data-testid="share-person"]')]
    await click(rows()[0].querySelector('[data-testid="share-seg-read"]'))
    await click(rows()[1].querySelector('button[aria-label="Ben entfernen"]'))
    expect(rows()).toHaveLength(1)
    expect(calls.some((call) => call.method === 'PUT' || call.method === 'DELETE')).toBe(false)
    await click(box.querySelector('[data-testid="share-cancel"]'))
    expect(closed).toHaveBeenCalledTimes(1)
    expect(calls.some((call) => call.method === 'PUT' || call.method === 'DELETE')).toBe(false)
    // Again, now with "Teilen": both changes go to the server.
    await dialog(withAnna, undefined, member, closed)
    await click(rows()[0].querySelector('[data-testid="share-seg-read"]'))
    await click(rows()[1].querySelector('button[aria-label="Ben entfernen"]'))
    await click(box.querySelector('[data-testid="share-submit"]'))
    expect(calls.find((call) => call.method === 'PUT' && call.path === '/api/calendars/9/sharing/2')?.body).toEqual({ right: 'read' })
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/calendars/9/sharing/3')).toBe(true)
    expect(closed).toHaveBeenCalledTimes(2)
  })
})

describe('TIME-19 the calendar menu and the list', () => {
  function app(calendars: CalendarEntry[]) {
    serve(
      signedIn(tester, (_method, path) => {
        if (path === '/api/calendars') return ok(calendarList(calendars))
        return undefined
      }),
    )
  }

  it('TIME-19 AC-8 AC-15 "Teilen …" stands only at calendars the member owns, never at ones shared with it', async () => {
    app([
      calendar(1, 'Familie'),
      calendar(2, 'Arbeit', { origin: { id: 4, kind: 'icloud', label: 'x', state: 'ok', synced_at: null } }),
      calendar(3, 'Ferien', { group: 'subscriptions', origin: { id: 5, kind: 'subscription', label: 'x', state: 'ok', synced_at: null } }),
      calendar(4, 'Garten', { group: 'shared', right: 'edit', owner: 'Anna' }),
    ])
    await render(<AuthProvider><App /></AuthProvider>, '/')
    const answers: Record<string, string[]> = {}
    for (const name of ['Familie', 'Arbeit', 'Ferien', 'Garten']) {
      const button = [...box.querySelectorAll<HTMLButtonElement>('button[aria-haspopup="menu"]')].find((each) => each.getAttribute('aria-label')?.includes(name))!
      await click(button)
      answers[name] = [...document.querySelectorAll('[data-testid="calendar-menu"] [role="menuitem"]')].map((item) => item.textContent ?? '')
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await settle()
    }
    expect(answers.Familie).toContain('Teilen …')
    expect(answers.Arbeit).toContain('Teilen …')
    expect(answers.Ferien).not.toContain('Teilen …')
    expect(answers.Garten).not.toContain('Teilen …')
    for (const name of ['Familie', 'Arbeit', 'Ferien', 'Garten']) expect(answers[name]).toContain('Einstellungen und Mitteilungen')
  })

  it('TIME-19 AC-1 a calendar shared with the member shows its owner', async () => {
    app([calendar(4, 'Garten', { group: 'shared', right: 'read', owner: 'Anna' })])
    await render(<AuthProvider><App /></AuthProvider>, '/')
    const row = [...box.querySelectorAll('[data-testid="calendar-toggle"]')].find((item) => item.textContent?.includes('Garten'))!
    expect(row.textContent).toBe('GartenAnna')
    expect(box.querySelector('[data-testid="calendar-group-shared"]')).not.toBeNull()
  })
})

describe('TIME-19 "Freigaben" under Settings', () => {
  function area(overview: SharesOverview) {
    let state = overview
    serve((method, path) => {
      if (path === '/api/shares') return ok(state)
      if (method === 'DELETE' && path.startsWith('/api/shares/notices/')) {
        state = { ...state, notices: [] }
        return ok({})
      }
      if (method === 'POST' && path.endsWith('/leave')) {
        state = { ...state, with_me: [] }
        return ok({})
      }
      if (method === 'DELETE') {
        state = { ...state, mine: state.mine.filter((row) => row.kind !== 'person') }
        return ok(sharing())
      }
      return undefined
    })
  }

  const full: SharesOverview = {
    mine: [
      { kind: 'person', calendar: { id: 5, name: 'Familie', color: 'green', kind: 'own' }, person: anna, right: 'edit', since: '2026-11-20T10:00:00Z' },
      { kind: 'link', calendar: { id: 5, name: 'Familie', color: 'green', kind: 'own' }, since: '2026-11-21T10:00:00Z', until: null, active: true },
      { kind: 'person', calendar: { id: 9, name: 'Arbeit', color: 'blue', kind: 'icloud' }, person: ben, right: 'busy', since: '2026-11-22T10:00:00Z' },
    ],
    with_me: [
      { calendar: { id: 12, name: 'Garten', color: 'teal', kind: 'own' }, owner: 'Anna', right: 'read' },
      { calendar: { id: 13, name: 'Annas Arbeit', color: 'blue', kind: 'icloud' }, owner: 'Anna', right: 'read' },
    ],
    notices: [{ id: 3, calendar: 'Verein', owner: 'Ben', color: 'red', at: '2026-11-25T10:00:00Z' }],
  }

  it('TIME-19 EC-6 without anything shared it says where sharing starts', async () => {
    area({ mine: [], with_me: [], notices: [] })
    await render(<SharesArea />)
    expect(box.querySelector('[data-testid="shares-empty"]')!.textContent).toBe(
      'Du teilst nichts, und niemand teilt etwas mit dir. Teilen geht über „Teilen …“ im Menü eines Kalenders.',
    )
  })

  it('TIME-19 AC-6 lists what is shared, with whom, right, since and until, and what is shared with the member', async () => {
    area(full)
    await render(<SharesArea />)
    const mine = [...box.querySelectorAll('[data-testid="shares-mine-row"]')].map((row) => row.textContent)
    expect(mine[0]).toContain('Familie')
    expect(mine[0]).toContain('Anna · bearbeiten')
    expect(mine[0]).toContain('seit 20.11.2026')
    expect(mine[1]).toContain('Gast-Link zum Lesen')
    expect(mine[1]).toContain('bis zum Widerruf')
    expect(mine[2]).toContain('Arbeit (iCloud)')
    expect(mine[2]).toContain('Ben · nur belegt')
    const withMe = box.querySelector('[data-testid="shares-with-me-row"]')!.textContent
    expect(withMe).toContain('Garten')
    expect(withMe).toContain('von Anna · lesen')
    // A calendar from iCloud shared whole says so, not "lesen" (AC-2).
    expect(box.querySelectorAll('[data-testid="shares-with-me-row"]')[1].textContent).toContain('von Anna · ganz zeigen')
    // Taking back asks first.
    await click(box.querySelector('[data-testid="shares-take-back"]'))
    expect(document.querySelector('[role="dialog"]')!.getAttribute('aria-label')).toBe('Freigabe für Anna zurücknehmen?')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(byText('Zurücknehmen', document.querySelector('[role="dialog"]')!))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/calendars/5/sharing/2')).toBe(true)
  })

  it('TIME-19 AC-4 leaving asks first and then the calendar goes from the list', async () => {
    area(full)
    await render(<SharesArea />)
    await click(box.querySelector('[data-testid="shares-leave"]'))
    expect(document.querySelector('[role="dialog"]')!.textContent).toContain('Anna kann ihn dir wieder freigeben.')
    await click(byText('Verlassen', document.querySelector('[role="dialog"]')!))
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/calendars/12/leave')).toBe(true)
    expect(box.querySelector('[data-testid="shares-with-me-row"]')).toBeNull()
  })

  it('TIME-19 AC-14 a calendar that went says so with "Ausblenden"', async () => {
    area(full)
    await render(<SharesArea />)
    const notice = box.querySelector('[data-testid="shares-notice"]')!
    expect(notice.textContent).toContain('„Verein“ ist nicht mehr da: Ben hat das Teilen beendet.')
    await click(notice.querySelector('[data-testid="shares-hide"]'))
    expect(calls.some((call) => call.method === 'DELETE' && call.path === '/api/shares/notices/3')).toBe(true)
    expect(box.querySelector('[data-testid="shares-notice"]')).toBeNull()
  })
})

describe('TIME-19 the guest page', () => {
  const page = (change: Partial<Page> = {}): Page => ({
    calendar: { name: 'Familie', color: 'green' },
    owner: 'Carla',
    events: [
      { all_day: false, start: '2026-11-27T17:00:00Z', end: '2026-11-27T18:00:00Z', title: 'Elternabend', location: 'Schule' },
      { all_day: true, start: '2026-11-28', end: '2026-11-28', title: 'Flohmarkt', location: '' },
    ],
    more: false,
    next: 2,
    feed: 'https://kalender.example.com/abo/q8Zt3f.ics',
    ...change,
  })

  async function guest(answer: (offset: number) => Page | null) {
    serve((_method, path) => {
      if (!path.startsWith('/api/guest/')) return undefined
      const offset = Number(new URL(path, 'http://x').searchParams.get('offset') ?? 0)
      const found = answer(offset)
      return found ? ok(found) : { status: 404, body: { detail: { code: 'link_invalid' } } }
    })
    document.documentElement.removeAttribute('data-theme')
    await render(
      <Routes>
        <Route path="abo/:token" element={<GuestPage />} />
      </Routes>,
      '/abo/q8Zt3f',
    )
  }

  it('TIME-19 AC-13 is light and shows name, owner, subscribe, copy, the sentence, the coming events by day and "Erstellt mit nextime"', async () => {
    await guest(() => page())
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(box.querySelector('[data-testid="guest-name"]')!.textContent).toBe('Familie')
    expect(box.querySelector('[data-testid="guest-owner"]')!.textContent).toBe('Geteilt von Carla · nur lesen')
    expect(box.querySelector<HTMLAnchorElement>('[data-testid="guest-subscribe"]')!.getAttribute('href')).toBe('webcal://kalender.example.com/abo/q8Zt3f.ics')
    expect(box.querySelector('[data-testid="guest-subscribe"]')!.textContent).toBe('In meinem Kalender abonnieren')
    expect(box.querySelector('[data-testid="guest-copy"]')!.textContent).toBe('Adresse kopieren')
    expect(box.textContent).toContain('Abonniert bleibt der Kalender auf deinem Gerät aktuell, bis Carla den Link zurücknimmt.')
    const rows = [...box.querySelectorAll('[data-testid="guest-event"]')].map((row) => row.textContent)
    expect(rows[0]).toContain('Elternabend')
    expect(rows[0]).toContain('Schule')
    expect(rows[1]).toContain('ganzt.')
    expect(rows[1]).toContain('Flohmarkt')
    expect(box.textContent).toContain('Erstellt mit nextime')
  })

  it('TIME-19 AC-17 shows more on request', async () => {
    const second = page({ events: [{ all_day: false, start: '2026-12-05T09:00:00Z', end: '2026-12-05T10:00:00Z', title: 'Adventsmarkt', location: '' }], more: false, next: 3 })
    await guest((offset) => (offset === 0 ? page({ more: true }) : second))
    await click(box.querySelector('[data-testid="guest-more"]'))
    expect(calls.some((call) => call.path.includes('offset=2'))).toBe(true)
    expect([...box.querySelectorAll('[data-testid="guest-event"]')]).toHaveLength(3)
    expect(box.querySelector('[data-testid="guest-more"]')).toBeNull()
  })

  it('TIME-19 EC-7 a calendar without coming events says so and can still be subscribed', async () => {
    await guest(() => page({ events: [] }))
    expect(box.querySelector('[data-testid="guest-none"]')!.textContent).toBe('Keine kommenden Termine. Abonnieren geht trotzdem.')
    expect(box.querySelector('[data-testid="guest-subscribe"]')).not.toBeNull()
  })

  it('TIME-19 EC-1 a link that no longer holds says only that, without a name', async () => {
    await guest(() => null)
    expect(box.querySelector('[data-testid="guest-gone"]')!.textContent).toBe('Dieser Link gilt nicht mehr.')
    expect(box.querySelector('[data-testid="guest-name"]')).toBeNull()
  })

  it('TIME-19 EC-10 code in the name, title or place stays text', async () => {
    await guest(() => page({ calendar: { name: '<img src=x onerror=alert(1)>', color: 'green' }, events: [{ all_day: false, start: '2026-11-27T17:00:00Z', end: '2026-11-27T18:00:00Z', title: '<script>x</script>', location: '<b>Ort</b>' }] }))
    expect(box.querySelector('img')).toBeNull()
    expect(box.querySelector('b')).toBeNull()
    expect(box.querySelector('[data-testid="guest-name"]')!.textContent).toBe('<img src=x onerror=alert(1)>')
    expect(box.querySelector('[data-testid="guest-event"]')!.textContent).toContain('<script>x</script>')
  })

  it('TIME-19 EC-23 in English for an English browser', async () => {
    await i18n.changeLanguage('en')
    await guest(() => page())
    expect(box.querySelector('[data-testid="guest-owner"]')!.textContent).toBe('Shared by Carla · read only')
    expect(box.querySelector('[data-testid="guest-subscribe"]')!.textContent).toBe('Subscribe in my calendar')
    expect(box.textContent).toContain('all day')
  })
})
