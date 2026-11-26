/**
 * Birthdays from nexfolk in "Heute" (draft, plan "Apps verbinden" V1 8a): over a connection made by a click, the next
 * days' birthdays and anniversaries with "heute", "morgen" or the day and the age; nothing without a connection; a
 * quiet line when nexfolk does not answer.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { BirthdaysBlock } from './features/nexfolk/BirthdaysBlock'
import i18n from './i18n'

let root: Root
let box: HTMLDivElement
let answer: unknown

beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  await i18n.changeLanguage('de')
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(answer), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})
afterEach(() => {
  act(() => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
})

async function show(): Promise<void> {
  await act(async () => root.render(<BirthdaysBlock today="2026-11-26" />))
  for (let i = 0; i < 5; i++) await act(async () => await Promise.resolve())
}

describe('birthdays from nexfolk', () => {
  it('lists the next days with today, tomorrow and the age', async () => {
    answer = { connected: true, problem: null, birthdays: [
      { day: '2026-11-26', name: 'Kim', kind: 'birthday', label: '', years: 40 },
      { day: '2026-11-27', name: 'Mara', kind: 'anniversary', label: 'Hochzeitstag', years: 5 },
      { day: '2026-11-30', name: 'Sam', kind: 'birthday', label: '', years: null },
    ] }
    await show()
    const rows = [...box.querySelectorAll('[data-testid="birthday"]')].map((row) => row.textContent)
    expect(box.querySelector('h2')!.textContent).toBe('Geburtstage aus nexfolk')
    expect(rows[0]).toBe('Kim wird 40 Jahre altheute')
    expect(rows[1]).toBe('Mara: Hochzeitstagmorgen')
    expect(rows[2]).toContain('Sam hat Geburtstag')
  })

  it('shows nothing without a connection, and a quiet line when nexfolk does not answer', async () => {
    answer = { connected: false, birthdays: [], problem: null }
    await show()
    expect(box.querySelector('[data-testid="birthdays-block"]')).toBeNull()
    answer = { connected: true, birthdays: [], problem: 'nexfolk_unreachable' }
    act(() => root.unmount())
    root = createRoot(box)
    await show()
    expect(box.querySelector('[data-testid="birthdays-problem"]')!.textContent).toBe('nexfolk antwortet gerade nicht.')
  })
})
