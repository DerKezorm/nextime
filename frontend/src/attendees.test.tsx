/**
 * Attendees named from nexfolk (draft, plan "Apps verbinden" V19/V29): an attendee shown only by its address gets the
 * name nexfolk knows for it; one with a name of its own, and the member itself, are not asked for.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'

import type { EventDetail } from './features/calendars/api'
import { Attendees } from './features/sources/Invitations'
import i18n from './i18n'

let root: Root
let box: HTMLDivElement
let asked: string[]

beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  await i18n.changeLanguage('de')
  asked = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    asked.push(String(input))
    return new Response(JSON.stringify({ connected: true, names: { 'kim@example.com': 'Kim Muster' } }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})
afterEach(() => {
  act(() => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
})

it('names an attendee shown by its address with the name nexfolk knows', async () => {
  const detail = {
    attendees: [
      { name: '', address: 'Kim@Example.com', state: 'accepted', organizer: false, self: false },
      { name: 'Sam', address: 'sam@example.com', state: 'needs-action', organizer: true, self: false },
      { name: '', address: 'ich@example.com', state: 'needs-action', organizer: false, self: true },
    ],
    attendees_count: 3,
  } as unknown as EventDetail
  await act(async () => root.render(<MemoryRouter><Attendees detail={detail} /></MemoryRouter>))
  for (let i = 0; i < 6; i++) await act(async () => await Promise.resolve())
  expect(asked).toEqual(['/api/people/names?addresses=kim%40example.com'])
  const names = [...box.querySelectorAll('[data-testid="attendee-name"]')].map((name) => name.textContent)
  expect(names[0]).toBe('Kim Muster')
  expect(names[1]).toContain('Sam')
})
