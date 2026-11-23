/**
 * TIME-1 AC-9, Settings › Log as in the other nex apps: a table of time, level, source, request and account,
 * message; level chips that show the higher levels too, a search, colors for WARNING and ERROR, cards on a phone
 * without a table to scroll sideways, older lines page by page.
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import i18n from '../../i18n'
import { LogCard } from './LogCard'

let root: Root
let box: HTMLDivElement
const asked: URLSearchParams[] = []

function line(number: number, level = 'INFO') {
  return { time: '2026-11-21 09:00:00', level, logger: 'nextime.notes', message: `line ${number}`, request_id: number === 1 ? 'a1b2c3d4' : null, user: number === 1 ? 'anna' : null, at: null }
}

beforeEach(async () => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  asked.length = 0
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), 'http://localhost')
      let body: unknown = []
      if (url.pathname === '/api/logs/level') body = { mode: 'normal', until: null, fixed_by_env: false, modes: ['quiet', 'normal', 'detailed', 'trace'] }
      else if (url.pathname === '/api/logs') {
        asked.push(url.searchParams)
        const offset = Number(url.searchParams.get('offset') ?? 0)
        const all = [line(1, 'ERROR'), line(2, 'WARNING'), ...Array.from({ length: 248 }, (_, index) => line(index + 3))]
        body = all.slice(offset, offset + Number(url.searchParams.get('limit')))
      }
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
  await i18n.changeLanguage('de')
})

afterEach(async () => {
  act(() => root.unmount())
  box.remove()
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

async function wait(ms = 320): Promise<void> {
  await act(async () => await new Promise((resolve) => setTimeout(resolve, ms)))
}

describe('TIME-1 AC-9 the log', () => {
  it('TIME-1 AC-9 is a table with time, level, source, request and account, message; WARNING orange, ERROR red', async () => {
    act(() => root.render(<LogCard />))
    await wait()
    const table = box.querySelector<HTMLTableElement>('[data-testid="log-table"]')!
    expect([...table.querySelectorAll('th')].map((cell) => cell.textContent)).toEqual(['Zeit', 'Stufe', 'Quelle', 'Nr. / Konto', 'Meldung'])
    const rows = [...table.querySelectorAll<HTMLElement>('[data-testid="log-row"]')]
    expect(rows).toHaveLength(200)
    expect(rows[0].textContent).toContain('a1b2c3d4')
    expect(rows[0].textContent).toContain('anna')
    expect(rows[0].querySelector('[data-testid="log-level"]')!.className).toContain('text-bad-500')
    expect(rows[1].querySelector('[data-testid="log-level"]')!.className).toContain('text-warn-500')
    expect(rows[2].querySelector('[data-testid="log-level"]')!.className).not.toMatch(/warn|bad/)
    // A phone: cards instead of the table, and nothing that scrolls sideways.
    expect(table.className).toMatch(/(^| )hidden( |$)/)
    expect(table.className).toContain('md:table')
    expect(table.className).toContain('table-fixed')
    expect(box.querySelector('[data-testid="log-cards"]')!.className).toContain('md:hidden')
    expect(box.querySelectorAll('[data-testid="log-card"]')).toHaveLength(200)
    expect(box.querySelector('.overflow-x-auto, .overflow-auto')).toBeNull()
    // The level of detail is in the bar, with the rest.
    expect(box.querySelector('[data-testid="log-bar"] [data-testid="log-mode"]')).not.toBeNull()
  })

  it('TIME-1 AC-9 filters by level (the higher ones too) and by text, and loads older lines page by page', async () => {
    act(() => root.render(<LogCard />))
    await wait()
    expect([...box.querySelectorAll('[role="radio"]')].map((chip) => chip.textContent)).toEqual(['Alle', 'INFO', 'WARNING', 'ERROR'])
    expect(box.textContent).toContain('Ein Filter zeigt immer auch die höheren Stufen')
    await act(async () => box.querySelector<HTMLButtonElement>('[data-testid="log-level-WARNING"]')!.click())
    await wait()
    expect(asked.at(-1)!.get('level')).toBe('WARNING')
    expect(box.querySelector('[data-testid="log-level-WARNING"]')!.getAttribute('aria-checked')).toBe('true')
    const search = box.querySelector<HTMLInputElement>('[data-testid="log-search"]')!
    expect(search.placeholder).toBe('Im Text suchen …')
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(search, 'line 7')
      search.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await wait()
    expect(asked.at(-1)!.get('search')).toBe('line 7')
    // Back to all, then older ones.
    await act(async () => box.querySelector<HTMLButtonElement>('[data-testid="log-level-all"]')!.click())
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(search, '')
      search.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await wait()
    await act(async () => box.querySelector<HTMLButtonElement>('[data-testid="log-more"]')!.click())
    await wait(20)
    expect(asked.at(-1)!.get('offset')).toBe('200')
    expect(box.querySelectorAll('[data-testid="log-row"]')).toHaveLength(250)
    expect(box.querySelector('[data-testid="log-more"]')).toBeNull()
  })
})
