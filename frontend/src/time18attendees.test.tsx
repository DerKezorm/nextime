/**
 * TIME-18 AC-9 (plan "Apps verbinden" V30): the field "Teilnehmer" adds a free mail address with Enter and a person
 * of nexfolk from the suggestions (`/api/people`), shows them as chips that can be removed, and refuses what is no
 * mail address.
 */
import { act, useEffect, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { AttendeesField, type Invitee } from './features/calendars/AttendeesField'
import i18n from './i18n'

let root: Root
let box: HTMLDivElement
const seen: { latest: Invitee[] } = { latest: [] }

beforeEach(async () => {
  seen.latest = []
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  await i18n.changeLanguage('de')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ connected: true, people: [{ id: 'c1', name: 'Kim Muster', emails: ['kim@example.com'] }] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})
afterEach(() => {
  act(() => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
})

function Host() {
  const [value, setValue] = useState<Invitee[]>([])
  useEffect(() => {
    seen.latest = value
  }, [value])
  return <AttendeesField value={value} onChange={setValue} />
}

async function type(text: string): Promise<void> {
  const input = box.querySelector<HTMLInputElement>('[data-testid="attendee-input"]')!
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, text)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
  for (let i = 0; i < 5; i++) await act(async () => await Promise.resolve())
}

async function enter(): Promise<void> {
  const input = box.querySelector<HTMLInputElement>('[data-testid="attendee-input"]')!
  await act(async () => void input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
}

it('adds a person of nexfolk and a free address, and removes one', async () => {
  await act(async () => root.render(<Host />))
  await type('kim')
  const suggestion = box.querySelector<HTMLButtonElement>('[data-testid="attendee-suggestion"]')!
  expect(suggestion.textContent).toContain('Kim Muster')
  await act(async () => suggestion.click())
  await type('sam@example.com')
  await enter()
  expect(seen.latest).toEqual([{ email: 'kim@example.com', name: 'Kim Muster' }, { email: 'sam@example.com', name: '' }])
  expect([...box.querySelectorAll('[data-testid="attendee-chip"]')].map((chip) => chip.textContent)).toEqual(['Kim Muster×', 'sam@example.com×'])
  await act(async () => box.querySelector<HTMLButtonElement>('[aria-label="Kim Muster entfernen"]')!.click())
  expect(seen.latest).toEqual([{ email: 'sam@example.com', name: '' }])
})

it('refuses what is no mail address', async () => {
  await act(async () => root.render(<Host />))
  await type('kein mail')
  await enter()
  expect(seen.latest).toEqual([])
  expect(box.querySelector('[data-testid="attendee-problem"]')!.textContent).toBe('Das ist keine Mailadresse.')
})
