/** TIME-4 custom colour (decided while testing, 2026-11-23, as nexnotes' "Symbol und Farbe"): below the eleven tiles
 * "Eigene Farbe" with the system's picker and a field `#rrggbb`; the chosen own color carries the check, none of the
 * eleven does; a wrong value says so and keeps the color; there is no "Ohne Farbe". An own color is drawn lifted in the
 * dark look and its text darker or lighter, so it reads on its tint. */
import { act } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { calendar, calendarList } from './calendarFakes'
import { colorVar, Swatch, textColorVar } from './features/calendars/Swatch'
import type { CalendarList } from './features/calendars/api'
import i18n from './i18n'
import { AuthProvider } from './state/auth'
import { box, calls, click, me, ok, render, serve, signedIn, start, stop, type } from './testing'

const fsName = 'node:fs'
const { readFileSync } = (await import(/* @vite-ignore */ fsName)) as { readFileSync: (file: string, encoding: string) => string }

const anna = me({ display: { week_start: 1, week_numbers: true, clock: 'auto', time_zone: 'Europe/Berlin', home_zone: 'Europe/Berlin' } })

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => {
  stop()
  vi.restoreAllMocks()
})

function server(list: CalendarList) {
  serve(signedIn(anna, (method, path) => (path === '/api/calendars' && method === 'POST' ? { status: 201, body: list } : path.startsWith('/api/calendars') ? ok(list) : undefined)))
}

async function openDialog(): Promise<HTMLElement> {
  await render(
    <AuthProvider>
      <App />
    </AuthProvider>,
    '/',
  )
  await click(box.querySelector('[data-testid="add-calendar"]'))
  await click(document.querySelector('[data-testid="way-own"]'))
  return document.querySelector<HTMLElement>('[data-testid="calendar-dialog"]')!
}

const enter = async (field: HTMLElement) => act(async () => void field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))

describe('TIME-4 custom colour', () => {
  it('TIME-4 custom colour the field takes #rrggbb: the own tile carries the check, none of the eleven, and it is sent', async () => {
    server(calendarList([calendar(1, 'Persönlich')]))
    const dialog = await openDialog()
    expect(dialog.textContent).toContain('Eigene Farbe')
    expect(dialog.textContent).not.toContain('Ohne Farbe')
    const hex = dialog.querySelector<HTMLInputElement>('[data-testid="color-hex"]')!
    expect(hex.placeholder).toBe('#rrggbb')
    type(hex, '#1E3A8A')
    await enter(hex)
    expect(dialog.querySelector('[data-testid="own-color-tile"]')!.getAttribute('data-chosen')).toBe('true')
    expect(dialog.querySelector('[data-testid="own-color-chosen"]')).not.toBeNull()
    expect([...dialog.querySelectorAll('[role="radio"][aria-checked="true"]')]).toHaveLength(0)
    // The eleven stay reachable by the keyboard: the first takes the tab stop.
    expect(dialog.querySelector('[data-testid="color-slate"]')!.getAttribute('tabindex')).toBe('0')
    expect(dialog.querySelector<HTMLInputElement>('[data-testid="color-picker"]')!.value).toBe('#1e3a8a')
    type(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!, 'Garten')
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    const sent = calls.find((call) => call.method === 'POST' && call.path === '/api/calendars')
    expect(sent?.body).toMatchObject({ name: 'Garten', color: '#1e3a8a' })
  })

  it('TIME-4 custom colour a wrong value says so and keeps the color; a tile of the eleven ends the own color', async () => {
    server(calendarList([calendar(1, 'Persönlich')]))
    const dialog = await openDialog()
    const hex = dialog.querySelector<HTMLInputElement>('[data-testid="color-hex"]')!
    type(hex, '#12345')
    await enter(hex)
    expect(dialog.querySelector('[data-testid="color-hex-wrong"]')!.textContent).toBe('Gib die Farbe als #rrggbb an, etwa #1e3a8a.')
    expect(hex.getAttribute('aria-invalid')).toBe('true')
    expect(dialog.querySelector('[data-testid="color-slate"]')!.getAttribute('aria-checked')).toBe('true')
    expect(dialog.querySelector('[data-testid="own-color-tile"]')!.getAttribute('data-chosen')).toBeNull()
    type(hex, 'a1b2c3')
    await enter(hex)
    expect(dialog.querySelector('[data-testid="own-color-tile"]')!.getAttribute('data-chosen')).toBe('true')
    await click(dialog.querySelector('[data-testid="color-teal"]'))
    expect(dialog.querySelector('[data-testid="color-teal"]')!.getAttribute('aria-checked')).toBe('true')
    expect(dialog.querySelector('[data-testid="own-color-tile"]')!.getAttribute('data-chosen')).toBeNull()
    expect(hex.value).toBe('')
  })

  it('TIME-4 custom colour the system picker chooses when it closes', async () => {
    server(calendarList([calendar(1, 'Persönlich')]))
    const dialog = await openDialog()
    const picker = dialog.querySelector<HTMLInputElement>('[data-testid="color-picker"]')!
    expect(picker.type).toBe('color')
    await act(async () => {
      picker.value = '#ff8800'
      picker.dispatchEvent(new Event('change', { bubbles: false }))
    })
    expect(dialog.querySelector('[data-testid="own-color-tile"]')!.getAttribute('data-chosen')).toBe('true')
    expect(dialog.querySelector<HTMLInputElement>('[data-testid="color-hex"]')!.value).toBe('#ff8800')
  })

  it('TIME-4 custom colour is drawn lifted in the dark look, its text readable in both, everywhere the calendar color goes', () => {
    expect(colorVar('#1e3a8a')).toBe('oklch(from #1e3a8a clamp(var(--own-min), l, var(--own-max)) c h)')
    expect(textColorVar('#1e3a8a')).toBe('oklch(from #1e3a8a clamp(var(--own-text-min), l, var(--own-text-max)) c h)')
    // The eleven stay the design system's.
    expect(colorVar('teal')).toBe('var(--color-cal-teal)')
    expect(textColorVar('teal')).toBe('var(--color-calt-teal)')
    // Strict in the browser too: neither an unknown name nor a loose hex reaches a style (review, 2026-11-23).
    expect(colorVar('red);background:url(x')).toBe('var(--color-cal-gray)')
    expect(colorVar('#12345')).toBe('var(--color-cal-gray)')
    expect(textColorVar('#ABCDEF')).toBe('var(--color-calt-gray)')
    const css = readFileSync('src/styles/index.css', 'utf-8')
    const dark = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')))
    // Dark: marks as light as the eleven's dark shades, text light; light: marks mid, text dark.
    expect(dark).toContain('--own-min: 0.7;')
    expect(dark).toContain('--own-text-min: 0.84;')
    const lightStart = css.indexOf(":root[data-theme='light'] {")
    const light = css.slice(lightStart, css.indexOf('}', lightStart))
    expect(light).toContain('--own-max: 0.62;')
    expect(light).toContain('--own-text-max: 0.4;')
    const host = document.createElement('div')
    document.body.append(host)
    act(() => createRoot(host).render(<Swatch color="#1e3a8a" />))
    expect(host.querySelector('[data-color]')!.getAttribute('data-color')).toBe('#1e3a8a')
    host.remove()
  })
})

describe('TIME-4 custom colour after the review', () => {
  it('TIME-4 custom colour a wrong value keeps "Anlegen" from saving the old color', async () => {
    server(calendarList([calendar(1, 'Persönlich')]))
    const dialog = await openDialog()
    type(dialog.querySelector<HTMLInputElement>('[data-testid="calendar-name-field"]')!, 'Garten')
    const hex = dialog.querySelector<HTMLInputElement>('[data-testid="color-hex"]')!
    type(hex, '#12345')
    await enter(hex)
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(calls.some((call) => call.method === 'POST' && call.path === '/api/calendars')).toBe(false)
    type(hex, '#123456')
    await enter(hex)
    await click(dialog.querySelector('[data-testid="calendar-save"]'))
    expect(calls.find((call) => call.method === 'POST' && call.path === '/api/calendars')?.body).toMatchObject({ color: '#123456' })
  })
})
