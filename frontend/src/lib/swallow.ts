/**
 * A click outside that closes a menu, popover or date picker closes only that (asked for while testing, 2026-11-23):
 * the press goes no further (no drag starts) and the click that follows it is swallowed, so nothing beneath opens a
 * new event or another one. As the menus of nexnotes.
 */
export function swallowClick(event: PointerEvent): void {
  event.stopPropagation()
  event.preventDefault()
  const swallow = (next: Event) => {
    next.stopPropagation()
    next.preventDefault()
  }
  window.addEventListener('click', swallow, { capture: true, once: true })
  window.setTimeout(() => window.removeEventListener('click', swallow, true), 700)
}
