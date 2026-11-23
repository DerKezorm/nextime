/**
 * The one menu of the app: over everything (a portal on `body`, so no box with a scroll bar cuts it off or grows one),
 * fixed beside the button or the point that opened it, upward when there is no room below, always inside the window.
 * The arrow keys go through the entries, Home and End to the first and last, Escape and Tab close it and give the
 * focus back to the button, a click outside closes it. Scrolling or a new window size moves it along.
 */
import { Check, type LucideIcon } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { swallowClick } from '../lib/swallow'

export type MenuAnchor = HTMLElement | { x: number; y: number }

const GAP = 4
const EDGE = 8

/** Where a menu of `size` stands for `anchor` in a window of `view`: below (or above), at the start or end. */
// eslint-disable-next-line react-refresh/only-export-components
export function menuPlace(
  anchor: { left: number; right: number; top: number; bottom: number },
  size: { width: number; height: number },
  view: { width: number; height: number },
  align: 'start' | 'end',
): { left: number; top: number; up: boolean } {
  let left = align === 'end' ? anchor.right - size.width : anchor.left
  left = Math.max(EDGE, Math.min(left, view.width - size.width - EDGE))
  const below = anchor.bottom + GAP
  const above = anchor.top - GAP - size.height
  const up = below + size.height > view.height - EDGE && above >= EDGE
  const top = up ? above : Math.max(EDGE, Math.min(below, view.height - size.height - EDGE))
  return { left, top, up }
}

export function Menu({
  anchor,
  onClose,
  label,
  children,
  align = 'end',
  width,
  className = '',
  testId,
}: {
  anchor: MenuAnchor
  onClose: () => void
  label: string
  children: ReactNode
  align?: 'start' | 'end'
  width?: number
  className?: string
  testId?: string
}) {
  const panel = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{ left: number; top: number; up: boolean } | null>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  }, [onClose])

  const measure = useCallback(() => {
    const element = panel.current
    if (!element) return
    const box =
      anchor instanceof HTMLElement ? anchor.getBoundingClientRect() : { left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y - GAP }
    setPlace(menuPlace(box, { width: element.offsetWidth, height: element.offsetHeight }, { width: window.innerWidth, height: window.innerHeight }, align))
  }, [anchor, align])

  useLayoutEffect(measure, [measure])

  // The first entry takes the focus once the menu stands where it belongs (a hidden one cannot).
  const placed = place !== null
  useEffect(() => {
    if (placed) panel.current?.querySelector<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])')?.focus()
  }, [placed])

  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const target = event.target as Node
      if (panel.current?.contains(target)) return
      // The button that opened it toggles it itself.
      if (anchor instanceof HTMLElement && anchor.contains(target)) return
      close.current()
      swallowClick(event)
    }
    window.addEventListener('pointerdown', outside, true)
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      window.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [anchor, measure])

  const keys = (event: React.KeyboardEvent) => {
    const items = [...(panel.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]') ?? [])]
    const at = items.indexOf(document.activeElement as HTMLElement)
    const go = (index: number) => items[(index + items.length) % items.length]?.focus()
    if (event.key === 'ArrowDown') go(at + 1)
    else if (event.key === 'ArrowUp') go(at - 1)
    else if (event.key === 'Home') go(0)
    else if (event.key === 'End') go(items.length - 1)
    else if (event.key === 'Escape' || event.key === 'Tab') {
      close.current()
      if (anchor instanceof HTMLElement) anchor.focus()
    } else return
    event.preventDefault()
    event.stopPropagation()
  }

  return createPortal(
    <div
      ref={panel}
      role="menu"
      aria-label={label}
      data-testid={testId}
      data-up={place?.up || undefined}
      onKeyDown={keys}
      className={'nt-menu fixed z-[60] max-h-[calc(100dvh-16px)] overflow-y-auto ' + className}
      style={{ left: place?.left ?? 0, top: place?.top ?? 0, width, visibility: place ? 'visible' : 'hidden' }}
    >
      {children}
    </div>,
    document.body,
  )
}

/** One entry of a menu, as nexnotes draws them: its symbol and its text; a dangerous one in red, on the same neutral
 * hover as the others. */
export function MenuEntry({ icon: Icon, danger = false, disabled, onClick, testId, children }: { icon?: LucideIcon; danger?: boolean; disabled?: boolean; onClick: () => void; testId?: string; children: ReactNode }) {
  return (
    <button type="button" role="menuitem" className={'nt-menu-item ' + (danger ? 'text-bad-500 hover:text-bad-500' : '')} disabled={disabled} onClick={onClick} data-testid={testId}>
      {Icon && <Icon aria-hidden className="h-4 w-4 shrink-0" strokeWidth={1.8} />}
      <span className="min-w-0 flex-1">{children}</span>
    </button>
  )
}

/** A choice among several in a menu, as nexnotes' view menu: the chosen one bold in the accent, its tick at the right. */
export function MenuChoice({ checked, onPick, testId, children }: { checked: boolean; onPick: () => void; testId?: string; children: ReactNode }) {
  return (
    <button type="button" role="menuitemradio" aria-checked={checked} className={'nt-menu-item ' + (checked ? 'font-semibold text-accent-400' : '')} onClick={onPick} data-testid={testId}>
      <span className="nt-wrap min-w-0 flex-1">{children}</span>
      <Check aria-hidden className={'h-4 w-4 shrink-0 ' + (checked ? '' : 'invisible')} />
    </button>
  )
}

/** The line between two parts of a menu, indented as in nexnotes. */
export function MenuLine() {
  return <div className="mx-2 my-1 h-px bg-ink-700" role="separator" />
}
