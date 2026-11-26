/**
 * "Notiz in nexnotes" in an event's panel (draft, plan "Apps verbinden" V19): only with a connection made by a click
 * with nexnotes; one click asks nextime to make the note there and says what came of it.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { forgetNexnotes, NoteButton } from './features/nexnotes/NoteButton'
import i18n from './i18n'

let root: Root
let box: HTMLDivElement
let connected: boolean
let posted: string[]

beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  await i18n.changeLanguage('de')
  forgetNexnotes()
  posted = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (init?.method === 'POST') posted.push(path)
    const body = path === '/api/nexnotes' ? { connected } : { made: true, title: 'Zahnarzt' }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
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

async function settle(): Promise<void> {
  for (let i = 0; i < 6; i++) await act(async () => await Promise.resolve())
}

describe('a note to an event in nexnotes', () => {
  it('shows nothing without a connection', async () => {
    connected = false
    await act(async () => root.render(<NoteButton eventId={7} occurrence={null} />))
    await settle()
    expect(box.querySelector('[data-testid="nexnotes-note"]')).toBeNull()
  })

  it('makes the note with one click and says so', async () => {
    connected = true
    await act(async () => root.render(<NoteButton eventId={7} occurrence="20261127T090000" />))
    await settle()
    const button = box.querySelector<HTMLButtonElement>('[data-testid="nexnotes-note-button"]')!
    expect(button.textContent).toBe('Notiz in nexnotes')
    await act(async () => button.click())
    await settle()
    expect(posted).toEqual(['/api/events/7/nexnotes?occurrence=20261127T090000'])
    expect(box.querySelector('[data-testid="nexnotes-note-state"]')!.textContent).toBe('In nexnotes angelegt, im Eingang.')
  })
})
