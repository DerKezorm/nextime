/** The one menu of the app: over everything, outside any box that scrolls, upward where there is no room below. */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { Menu, menuPlace } from './Menu'

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
})

describe('the menu', () => {
  it('hangs on the page itself, outside the box that scrolls, and keyboard and Escape work there', () => {
    const scroller = document.createElement('div')
    scroller.style.overflow = 'auto'
    scroller.className = 'overflow-x-auto'
    const button = document.createElement('button')
    scroller.append(button)
    document.body.append(scroller)
    const closed = vi.fn()
    act(() =>
      root.render(
        <Menu anchor={button} onClose={closed} label="Actions">
          <button type="button" role="menuitem">One</button>
          <button type="button" role="menuitem">Two</button>
        </Menu>,
      ),
    )
    const menu = document.querySelector<HTMLElement>('[role="menu"]')!
    expect(menu.parentElement).toBe(document.body)
    expect(scroller.contains(menu)).toBe(false)
    expect(menu.closest('.overflow-x-auto')).toBeNull()
    expect(menu.className).toContain('fixed')
    expect(document.activeElement?.textContent).toBe('One')
    act(() => void menu.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })))
    expect(document.activeElement?.textContent).toBe('Two')
    act(() => void menu.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })))
    expect(closed).toHaveBeenCalled()
    expect(document.activeElement).toBe(button)
    scroller.remove()
  })

  it('opens below, upward when there is no room below, and never leaves the window', () => {
    const view = { width: 1280, height: 800 }
    const size = { width: 240, height: 300 }
    expect(menuPlace({ left: 1000, right: 1032, top: 100, bottom: 132 }, size, view, 'end')).toEqual({ left: 792, top: 136, up: false })
    expect(menuPlace({ left: 1000, right: 1032, top: 700, bottom: 732 }, size, view, 'end')).toEqual({ left: 792, top: 396, up: true })
    // A phone: no room for it beside the button at the right edge, so it stays inside the window.
    expect(menuPlace({ left: 350, right: 382, top: 100, bottom: 132 }, size, { width: 390, height: 844 }, 'start').left).toBe(390 - 240 - 8)
    // Too tall for above and below: still inside the window.
    expect(menuPlace({ left: 10, right: 40, top: 300, bottom: 330 }, { width: 200, height: 780 }, view, 'start')).toEqual({ left: 10, top: 12, up: false })
  })
})
