/** The mark "Blatt mit Weiter" in both looks of the app, and the wordmark NEX/TIME (design system "Logo und Name"). */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { Logo, LOGO_COLORS, LogoMark } from './Logo'

let root: Root
let box: HTMLDivElement

beforeEach(() => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})

afterEach(() => {
  act(() => root.unmount())
  box.remove()
  document.documentElement.removeAttribute('data-theme')
})

const part = (name: string) => box.querySelector(`[data-part="${name}"]`)!

describe('the logo', () => {
  it('draws the mark in the colors of the look the app shows, and follows a switch', async () => {
    act(() => root.render(<LogoMark size={44} />))
    expect(box.querySelector('svg')!.dataset.look).toBe('dark')
    expect(part('tile').getAttribute('fill')).toBe('#0e1118')
    expect(part('arrow').getAttribute('stroke')).toBe('#c3d2ea')
    expect([...box.querySelectorAll('stop')].map((stop) => stop.getAttribute('stop-color'))).toEqual([...LOGO_COLORS.dark.gradient])
    await act(async () => {
      document.documentElement.setAttribute('data-theme', 'light')
      await Promise.resolve()
    })
    expect(part('tile').getAttribute('fill')).toBe('#ffffff')
    expect(part('arrow').getAttribute('stroke')).toBe('#3d5682')
    expect([...box.querySelectorAll('stop')].map((stop) => stop.getAttribute('stop-color'))).toEqual(['#a9bcdc', '#4a6491', '#26344f'])
    expect(part('rings')).not.toBeNull()
    expect(part('header')).not.toBeNull()
  })

  it('keeps the full mark with rings at 26 px, as in the sidebar', () => {
    act(() => root.render(<LogoMark size={26} />))
    expect(part('rings')).not.toBeNull()
    expect(part('header')).not.toBeNull()
  })

  it('leaves out the header below 24 px, keeps the rings and draws the sheet in one tone', () => {
    act(() => root.render(<LogoMark size={16} />))
    expect(part('rings')).not.toBeNull()
    expect(part('rings').getAttribute('stroke')).toBe(LOGO_COLORS.dark.small)
    expect(box.querySelector('[data-part="header"]')).toBeNull()
    expect(part('sheet').getAttribute('stroke')).toBe(LOGO_COLORS.dark.small)
    expect(box.querySelector('linearGradient')).toBeNull()
    expect(part('arrow')).not.toBeNull()
  })

  it('writes the name as NEX in the text color and TIME in the accent', () => {
    act(() => root.render(<Logo withWordmark />))
    const mark = box.querySelector('[data-testid="wordmark"]')!
    expect(mark.getAttribute('aria-label')).toBe('nextime')
    const [nex, time] = [...mark.children] as HTMLElement[]
    expect(nex.textContent).toBe('NEX')
    expect(time.textContent).toBe('TIME')
    expect(mark.className).toContain('text-mist-100')
    expect(time.className).toBe('nt-wordmark-accent')
  })
})
