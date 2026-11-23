/** TIME-12, the jump to a date (asked for while testing): the title of the head
 * opens a small month; a day chosen there shows in the view (Day the day, Week its week, Month its month), from "Heute"
 * as a day; key G opens it, a date field in "Zu Kalenderwoche springen" does the same. Today is Thursday 26 November
 * 2026 (`viewFakes`). */
import { act } from 'react'

import App from './App'
import i18n from './i18n'
import { forgetPrefs } from './lib/devicePref'
import { setViewDay } from './features/views/state'
import { AuthProvider } from './state/auth'
import { me, render, settle, start, stop, type } from './testing'
import { BERLIN, NOW, theWeek, views, type Fake } from './viewFakes'

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  start()
  forgetPrefs()
  localStorage.clear()
  setViewDay(null)
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

async function open(where: string, display: Partial<typeof BERLIN> = {}): Promise<void> {
  const fake: Fake = { rows: theWeek(), more: (_method, path) => (path === '/api/auth/me' ? { status: 200, body: me({ id: 1, display: { ...BERLIN, ...display } }) } : undefined) }
  views(fake)
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    where,
  )
}

const one = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const text = (id: string) => (one(id)?.textContent ?? '').replaceAll(String.fromCharCode(0x2009), ' ')
const picker = () => one('date-picker')
const day = (date: string) => picker()!.querySelector<HTMLButtonElement>(`[data-date="${date}"]`)!
const press = async (key: string, target: EventTarget = window) =>
  act(async () => void target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true })))
async function tap(element: Element | null): Promise<void> {
  await act(async () => (element as HTMLElement).click())
  await settle()
}
async function choose(select: HTMLSelectElement, value: string): Promise<void> {
  await act(async () => {
    select.value = value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

describe('TIME-12 jump date', () => {
  it('TIME-12 jump date the title opens the month shown, with weeks and the first weekday as set, the weekend tinted', async () => {
    await open('/month')
    await tap(one('period'))
    expect(picker()?.getAttribute('role')).toBe('dialog')
    expect(one('period')?.getAttribute('aria-expanded')).toBe('true')
    const heads = [...picker()!.querySelectorAll('[role="columnheader"]')].map((node) => node.textContent)
    expect(heads).toEqual(['KW', 'M', 'D', 'M', 'D', 'F', 'S', 'S'])
    expect([...picker()!.querySelectorAll('[data-testid="pick-week"]')].map((node) => node.textContent)).toEqual(['44', '45', '46', '47', '48', '49'])
    expect(day('2026-11-26').getAttribute('aria-current')).toBe('date')
    expect(day('2026-11-26').getAttribute('tabindex')).toBe('0')
    expect(day('2026-11-28').className).toContain('nt-weekend')
    expect(day('2026-11-27').className).not.toContain('nt-weekend')
  })

  it('TIME-12 jump date follows "week begins on Sunday" and "no calendar weeks"', async () => {
    await open('/month', { week_start: 0, week_numbers: false })
    await tap(one('period'))
    const heads = [...picker()!.querySelectorAll('[role="columnheader"]')].map((node) => node.textContent)
    expect(heads).toEqual(['S', 'M', 'D', 'M', 'D', 'F', 'S'])
    expect(picker()!.querySelector('[data-testid="pick-week"]')).toBeNull()
  })

  it('TIME-12 jump date a day goes into the view shown: the week its week, the month its month, the day the day', async () => {
    await open('/week')
    await tap(one('period'))
    await tap(one('pick-on'))
    await tap(day('2026-12-16'))
    expect(picker()).toBeNull()
    expect(one('view-week')).not.toBeNull()
    expect(text('period')).toBe('14.–20. Dezember 2026')
    await press('m')
    expect(text('period')).toBe('Dezember 2026')
    await tap(one('period'))
    await choose(one('pick-year') as HTMLSelectElement, '2028')
    await choose(one('pick-month') as HTMLSelectElement, '2')
    await tap(day('2028-02-29'))
    expect(text('period')).toBe('Februar 2028')
    await press('t')
    expect(text('period')).toBe('Dienstag, 29. Februar 2028')
    await tap(one('period'))
    await tap(one('pick-today'))
    expect(text('period')).toBe('Donnerstag, 26. November 2026')
  })

  it('TIME-12 jump date key G opens it, not while typing; Escape closes it; the arrows move by day and week', async () => {
    await open('/day')
    const field = document.createElement('input')
    document.body.appendChild(field)
    await press('g', field)
    expect(picker()).toBeNull()
    field.remove()
    await press('g')
    expect(picker()).not.toBeNull()
    // While it is open, the keys of the views wait: K opens no second dialog.
    await press('k')
    expect(one('week-jump')).toBeNull()
    const focused = () => (document.activeElement as HTMLElement | null)?.dataset.date
    expect(focused()).toBe('2026-11-26')
    await press('ArrowRight', document.activeElement!)
    await act(async () => new Promise((done) => requestAnimationFrame(() => done(undefined))))
    expect(focused()).toBe('2026-11-27')
    await press('ArrowDown', document.activeElement!)
    await act(async () => new Promise((done) => requestAnimationFrame(() => done(undefined))))
    expect(focused()).toBe('2026-12-04')
    expect(picker()!.querySelector('[role="grid"]')?.getAttribute('aria-label')).toBe('Dezember 2026')
    await press('Escape', document.activeElement!)
    expect(picker()).toBeNull()
    expect(document.activeElement).toBe(one('period'))
  })

  it('TIME-12 jump date from "Heute" a day opens as a day', async () => {
    await open('/')
    await tap(one('today-pick'))
    await tap(day('2026-11-30'))
    expect(one('view-day')).not.toBeNull()
    expect(text('period')).toBe('Montag, 30. November 2026')
  })

  it('TIME-12 jump date "Zu Kalenderwoche springen" takes a date too, which wins over the week', async () => {
    await open('/month')
    await press('k')
    expect(one('jump-date')?.className).toContain('nt-field')
    type(one('jump-date') as HTMLInputElement, '2027-03-15')
    await act(async () => (one('week-jump') as HTMLFormElement).requestSubmit())
    await settle()
    expect(text('period')).toBe('März 2027')
    await press('t')
    expect(text('period')).toBe('Montag, 15. März 2027')
  })
})

describe('TIME-12 jump date after the review', () => {
  it('TIME-12 jump date Tab stays inside, a day taken gives the focus back to the title', async () => {
    await open('/month')
    one('period')!.focus()
    await tap(one('period'))
    const today = one('pick-today')!
    today.focus()
    const tab = async (shift = false) =>
      act(async () => void document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: shift, bubbles: true, cancelable: true })))
    await tab()
    expect(picker()!.contains(document.activeElement)).toBe(true)
    expect(document.activeElement).toBe(one('pick-back'))
    await tab(true)
    expect(document.activeElement).toBe(today)
    await tap(day('2026-11-12'))
    expect(picker()).toBeNull()
    expect(document.activeElement).toBe(one('period'))
  })
})
