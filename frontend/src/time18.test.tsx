/** TIME-18 in the interface: the sign "1 Einladung" in the head of "Heute", invitations dashed in Today and Week, the
 * side panel of an invitation with its answers and the attendees, answering once, a series, a changed invitation, and
 * deleting an open one. */
import { act } from 'react'

import App from './App'
import { calendar, calendarList, eventDetail } from './calendarFakes'
import type { Attendee, EventDetail, InvitationDetail } from './features/calendars/api'
import type { Invitation } from './features/sources/api'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { calls, click, ok, render, settle, start, stop, type Answer } from './testing'
import { at, NOW, views } from './viewFakes'
import { AuthProvider } from './state/auth'

const LIST = calendarList([
  calendar(1, 'Familie', { default: true }),
  calendar(11, 'Arbeit', { color: 'teal', group: 'icloud', origin: { id: 1, kind: 'icloud', label: 'familie@example.com', state: 'ok', synced_at: '2026-11-26T15:00:00Z' } }),
])

const OPEN: InvitationDetail = { state: 'open', by: 'Chris', cancelled: false, source: 'icloud', can_answer: true, why_not: '' }

function people(): Attendee[] {
  return [
    { name: 'Chris', address: 'chris@example.com', state: 'accepted', organizer: true, self: false },
    { name: 'Mara', address: 'familie@example.com', state: 'open', organizer: false, self: true },
    { name: 'Anna', address: 'anna@example.com', state: 'tentative', organizer: false, self: false },
    { name: 'Ben', address: 'ben@example.com', state: 'declined', organizer: false, self: false },
  ]
}

function invitation(change: Partial<EventDetail> = {}): EventDetail {
  return eventDetail({
    id: 50,
    calendar_id: 11,
    title: 'Projektplanung',
    location: 'Raum Süd',
    notes: 'Bitte die Zahlen mitbringen.',
    start: '2026-11-27T10:00',
    end: '2026-11-27T11:00',
    source: 'icloud',
    attendees: people(),
    attendees_count: 4,
    invitation: OPEN,
    ...change,
  })
}

const ROW = at(11, 'Projektplanung', '2026-11-27 10:00', '2026-11-27 11:00', { id: 50, invitation: { state: 'open', by: 'Chris' } })
const INVITE: Invitation = { id: 50, occurrence: null, calendar_id: 11, title: 'Projektplanung', start: ROW.start, end: ROW.end, all_day: false, by: 'Chris' }

type More = (method: string, path: string, body: unknown) => Answer | undefined

function server(detail: EventDetail, more: More = () => undefined, invites: Invitation[] = [INVITE], rows = [ROW]) {
  views({
    rows,
    calendars: LIST,
    more: (method, path, body) => {
      const extra = more(method, path, body)
      if (extra) return extra
      if (path.startsWith('/api/invitations')) return ok(invites)
      if (path === '/api/sources') return ok({ sources: [], notices: [], events_total: 0, events_smooth_up_to: 20000, google: { ready: false, configured: false } })
      if (/^\/api\/events\/50(\?|$)/.test(path) && method === 'GET') return ok(detail)
      return undefined
    },
  })
}

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

async function open(at = '/'): Promise<void> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    at,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const all = (id: string) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)]
const answers = () => calls.filter((call) => call.method === 'POST' && call.path === '/api/events/50/answer')

async function openInvitation(): Promise<void> {
  await open()
  await click(one('invitation-sign'))
}

describe('TIME-18 invitations in the views', () => {
  it('TIME-18 AC-1 the sign "1 Einladung" in the head of Today, the invitation dashed as "Einladung: <Titel>"', async () => {
    server(invitation())
    await open()
    expect(one('invitation-sign')!.textContent).toBe('1 Einladung')
    expect(one('invitation-sign')!.className).toContain('nt-invitation-sign')
    const title = one('invitation-title')!
    expect(title.textContent).toBe('Einladung: Projektplanung')
    expect(title.className).toContain('nt-invitation-text')
  })

  it('TIME-18 AC-1 in Day and Week the invitation stands dashed with "Einladung · <Person>"', async () => {
    server(invitation())
    await open('/week')
    const block = all('grid-event').find((event) => event.textContent?.includes('Projektplanung'))!
    expect(block.className).toContain('nt-invitation')
    expect(block.querySelector('[data-testid="grid-event-sub"]')!.textContent).toBe('Einladung · Chris')
  })

  it('TIME-18 AC-7 after "Vielleicht" dashed without "Einladung:", after "Zusagen" like any event', async () => {
    const maybe = { ...ROW, invitation: { state: 'tentative' as const, by: 'Chris' } }
    const yes = at(11, 'Mittagessen', '2026-11-27 12:00', '2026-11-27 13:00', { invitation: { state: 'accepted', by: 'Chris' } })
    server(invitation(), undefined, [], [maybe, yes])
    await open()
    expect(one('invitation-sign')).toBeNull()
    expect(all('invitation-title').map((title) => title.textContent)).toEqual(['Projektplanung'])
    await open('/week')
    const blocks = all('grid-event')
    expect(blocks.find((event) => event.textContent?.includes('Projektplanung'))!.className).toContain('nt-invitation')
    expect(blocks.find((event) => event.textContent?.includes('Mittagessen'))!.className).not.toContain('nt-invitation')
  })

  it('TIME-18 AC-6 one invitation opens in the side panel; several show as a list, each leading into the panel', async () => {
    const second: Invitation = { ...INVITE, id: 51, title: 'Elternabend', by: 'Schule' }
    server(invitation(), undefined, [INVITE, second])
    await open()
    expect(one('invitation-sign')!.textContent).toBe('2 Einladungen')
    await click(one('invitation-sign'))
    expect(all('invitation-entry').map((entry) => entry.querySelector('span')!.textContent)).toEqual(['Projektplanung', 'Elternabend'])
    expect(all('invitation-entry')[1].textContent).toContain('von Schule')
    await click(all('invitation-entry')[0])
    expect(one('invitation-list')).toBeNull()
    expect(one('invitation')).not.toBeNull()
  })
})

describe('TIME-18 the side panel of an invitation', () => {
  it('TIME-18 AC-4 calendar and source, "Einladung offen", title, time and place, the inviting person, three answers, where the answer goes, the note', async () => {
    server(invitation())
    await openInvitation()
    expect(one('invitation-source')!.textContent).toBe('Arbeit · iCloud')
    expect(one('invitation-open')!.textContent).toBe('Einladung offen')
    expect(one('invitation-open')!.className).toContain('nt-mark-open')
    expect(one('invitation-title-full')!.textContent).toBe('Projektplanung')
    expect(one('invitation-when')!.textContent).toBe('Fr., 27. November · 10:00–11:00 · Raum Süd')
    expect(one('invitation-by')!.textContent).toBe('Eingeladen von Chris')
    expect([...one('answers')!.querySelectorAll('button')].map((button) => button.textContent)).toEqual(['Zusagen', 'Vielleicht', 'Absagen'])
    expect([...one('answers')!.querySelectorAll('button')].map((button) => button.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'false'])
    expect(one('answer-goes')!.textContent).toBe('Die Antwort geht über iCloud an Chris.')
    expect(one('invitation-notes')!.textContent).toBe('Bitte die Zahlen mitbringen.')
  })

  it('TIME-18 AC-3 attendees with count, initial, name and state; the inviting one "(lädt ein)", the member "Du"', async () => {
    server(invitation())
    await openInvitation()
    expect(one('attendees')!.querySelector('h3')!.textContent).toBe('Teilnehmende · 4')
    expect(all('attendee').map((person) => person.textContent)).toEqual(['CChris (lädt ein)zugesagt', 'MDuoffen', 'AAnnavielleicht', 'BBenabgesagt'])
  })

  it('TIME-18 EC-8 with 200 attendees the number stands on top, the first eight show, "+192 weitere" opens the rest; long names are cut', async () => {
    const many: Attendee[] = Array.from({ length: 200 }, (_, index) => ({ name: index === 0 ? 'Ein sehr langer Name '.repeat(8) : `Person ${index}`, address: `p${index}@example.com`, state: 'open', organizer: false, self: false }))
    server(invitation({ attendees: many, attendees_count: 200 }))
    await openInvitation()
    expect(one('attendees')!.querySelector('h3')!.textContent).toBe('Teilnehmende · 200')
    expect(all('attendee')).toHaveLength(8)
    expect(all('attendee-name')[0].className).toContain('truncate')
    expect(all('attendee-name')[0].getAttribute('title')).toBe('Ein sehr langer Name '.repeat(8))
    await click(one('attendees-more'))
    expect(all('attendee')).toHaveLength(200)
  })

  it('TIME-18 AC-2 AC-5 an answer goes out, the chosen one shows pressed and can be changed', async () => {
    let state: InvitationDetail['state'] = 'open'
    server(invitation(), (method, path, body) => {
      if (path === '/api/events/50/answer' && method === 'POST') {
        state = (body as { answer: InvitationDetail['state'] }).answer
        return ok(invitation({ invitation: { ...OPEN, state } }))
      }
      return undefined
    })
    await openInvitation()
    await click(one('answer-accepted'))
    expect(answers()[0].body).toEqual({ answer: 'accepted', scope: 'all', etag: '"e1"' })
    expect(one('answer-accepted')!.getAttribute('aria-pressed')).toBe('true')
    expect(one('invitation-open')).toBeNull()
    await click(one('answer-tentative'))
    expect(answers()[1].body).toMatchObject({ answer: 'tentative' })
    expect(one('answer-tentative')!.getAttribute('aria-pressed')).toBe('true')
    expect(one('answer-accepted')!.getAttribute('aria-pressed')).toBe('false')
  })

  it('TIME-18 AC-7 after "Absagen" the panel closes and a short message says the invitation stays in the search', async () => {
    server(invitation(), (method, path) => (path === '/api/events/50/answer' && method === 'POST' ? ok(invitation({ invitation: { ...OPEN, state: 'declined' } })) : undefined))
    await openInvitation()
    await click(one('answer-declined'))
    expect(answers()[0].body).toMatchObject({ answer: 'declined' })
    expect(one('event-panel')).toBeNull()
    expect(one('toast')!.textContent).toBe('„Projektplanung“ abgesagt. In der Suche bleibt die Einladung.')
  })

  it('TIME-18 EC-5 two clicks on an answer send one', async () => {
    server(invitation(), (method, path) => (path === '/api/events/50/answer' && method === 'POST' ? ok(invitation({ invitation: { ...OPEN, state: 'accepted' } })) : undefined))
    await openInvitation()
    const accept = one('answer-accepted')!
    act(() => {
      accept.click()
      accept.click()
    })
    await settle()
    expect(answers()).toHaveLength(1)
  })

  it('TIME-18 EC-1 an invitation changed meanwhile shows its new state instead of an error', async () => {
    let changed = false
    server(invitation(), (method, path) => {
      if (path === '/api/events/50/answer' && method === 'POST') {
        changed = true
        return { status: 409, body: { detail: { code: 'invitation_changed', message: 'No.' } } }
      }
      if (/^\/api\/events\/50(\?|$)/.test(path) && method === 'GET' && changed) return ok(invitation({ start: '2026-11-27T14:00', end: '2026-11-27T15:00', etag: '"e2"' }))
      return undefined
    })
    await openInvitation()
    await click(one('answer-accepted'))
    expect(one('invitation-note')!.textContent).toBe('Die Einladung hat sich inzwischen geändert. Hier steht der neue Stand.')
    expect(one('invitation-when')!.textContent).toContain('14:00–15:00')
    expect(document.querySelector('[data-testid="event-panel"] [role="alert"]')).toBeNull()
    expect(one('panel-message')).toBeNull()
  })

  it('TIME-18 EC-2 where no answer can go out the buttons are missing, with the reason', async () => {
    server(invitation({ invitation: { ...OPEN, source: 'caldav', can_answer: false, why_not: 'no_replies' } }))
    await openInvitation()
    expect(one('answers')).toBeNull()
    expect(one('no-answers')!.textContent).toBe('Dieser CalDAV-Server nimmt keine Antworten an. Antworte in der App deines Kontos.')
  })

  it('TIME-18 EC-2 attendees from a subscription show without answers, with a hint', async () => {
    server(invitation({ invitation: null, writable: false, no_answers: 'read_only_source', source: 'subscription' }))
    await open()
    await act(async () => undefined)
    await click(all('column-row').find((entry) => entry.textContent?.includes('Projektplanung')))
    expect(one('answers')).toBeNull()
    expect(all('attendee')).toHaveLength(4)
    expect(one('no-answers')!.textContent).toBe('Über einen Abo-Link gehen keine Antworten hinaus.')
  })

  it('TIME-18 EC-6 an answer to a series asks whether it is for this event or all', async () => {
    server(invitation({ recurring: true, occurrence: '20261127T090000Z' }), (method, path) =>
      path === '/api/events/50/answer' && method === 'POST' ? ok(invitation({ recurring: true, occurrence: '20261127T090000Z', invitation: { ...OPEN, state: 'accepted' } })) : undefined,
    )
    await openInvitation()
    await click(one('answer-accepted'))
    expect(answers()).toHaveLength(0)
    expect(one('answer-series')!.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('Für welche Termine gilt die Antwort?')
    await click(one('answer-this'))
    expect(answers()[0].body).toEqual({ answer: 'accepted', scope: 'this', occurrence: '20261127T090000Z', etag: '"e1"' })
  })

  it('TIME-18 AC-8 deleting an open invitation asks "Stattdessen absagen?"; "Absagen" answers', async () => {
    server(invitation(), (method, path) => (path === '/api/events/50/answer' && method === 'POST' ? ok(invitation({ invitation: { ...OPEN, state: 'declined' } })) : undefined))
    await openInvitation()
    await click(one('panel-delete'))
    const question = one('decline-instead')!
    expect(question.closest('[role="dialog"]')!.getAttribute('aria-label')).toBe('Stattdessen absagen?')
    expect(question.textContent).toContain('Ohne Antwort weiß Chris nicht, dass du nicht kommst.')
    expect([...question.querySelectorAll('button')].map((button) => button.textContent)).toEqual(['Abbrechen', 'Ohne Antwort löschen', 'Absagen'])
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    await click(one('decline-instead-go'))
    expect(answers()[0].body).toMatchObject({ answer: 'declined' })
  })

  it('TIME-18 AC-8 "Ohne Antwort löschen" deletes without an answer', async () => {
    server(invitation(), (method, path) => (path.startsWith('/api/events/50') && method === 'DELETE' ? { status: 204, body: null } : undefined))
    await openInvitation()
    await click(one('panel-delete'))
    await click(one('delete-without-answer'))
    expect(calls.filter((call) => call.method === 'DELETE').map((call) => call.path)).toEqual(['/api/events/50?scope=all'])
    expect(answers()).toHaveLength(0)
  })

  it('TIME-18 EC-7 markup from a stranger stays text', async () => {
    const evil = '<img src=x onerror=alert(1)><a href="javascript:alert(2)">klick</a>'
    server(invitation({ title: evil, location: evil, notes: evil, invitation: { ...OPEN, by: evil }, attendees: [{ name: evil, address: 'x@example.com', state: 'open', organizer: false, self: false }] }))
    await openInvitation()
    const panel = one('invitation')!
    expect(one('invitation-title-full')!.textContent).toBe(evil)
    expect(one('invitation-by')!.textContent).toBe(`Eingeladen von ${evil}`)
    expect(panel.querySelector('img')).toBeNull()
    expect([...panel.querySelectorAll('a')].some((link) => (link.getAttribute('href') ?? '').startsWith('javascript:'))).toBe(false)
  })

  it('TIME-18 AC-3 an ordinary event with attendees shows them too', async () => {
    server(invitation({ invitation: null }))
    await open()
    await click(all('column-row').find((entry) => entry.textContent?.includes('Projektplanung')))
    expect(one('invitation')).toBeNull()
    expect(one('field-title')).not.toBeNull()
    expect(all('attendee')).toHaveLength(4)
  })
})
