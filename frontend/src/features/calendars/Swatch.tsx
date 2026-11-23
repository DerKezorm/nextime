/**
 * A calendar's mark: its symbol in its color, as nexnotes' `FolderMark` draws a folder (TIME-4, "Kalender-Symbol wie
 * nexnotes"); without a symbol its color as a small square, filled, or only its edge while the calendar is hidden.
 */
import { createElement, type CSSProperties } from 'react'

import { symbolIcon } from '../../lib/symbolLook'
import { CALENDAR_COLORS, type CalendarColor } from './api'

/** An own color as the server keeps it (TIME-4, as nexnotes' "Eigene Farbe"). */
export const OWN_COLOR = /^#[0-9a-f]{6}$/

// eslint-disable-next-line react-refresh/only-export-components
export function isOwnColor(color: string | null | undefined): color is `#${string}` {
  return typeof color === 'string' && OWN_COLOR.test(color)
}

/** One of the eleven by name, anything else gray: a value from elsewhere never reaches a style (review, 2026-11-23). */
function paletteName(color: string): string {
  return (CALENDAR_COLORS as readonly string[]).includes(color) ? color : 'gray'
}

/** `#RRGGBB`, `rrggbb` or `#rrggbb` as kept (small letters), else null. */
// eslint-disable-next-line react-refresh/only-export-components
export function hexOf(text: string): `#${string}` | null {
  const value = text.trim().toLowerCase()
  const full = value.startsWith('#') ? value : `#${value}`
  return OWN_COLOR.test(full) ? (full as `#${string}`) : null
}

/**
 * The color of a calendar for marks, dots and tints. One of the eleven comes from the design system in both looks; an
 * own color keeps its hue and chroma, its lightness held in the band of the eleven of each look (OKLCH, `--own-min`
 * and `--own-max`): lighter in the dark look, as the eleven have a lighter dark shade.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function colorVar(color: CalendarColor | string): string {
  if (isOwnColor(color)) return `oklch(from ${color} clamp(var(--own-min), l, var(--own-max)) c h)`
  return `var(--color-cal-${paletteName(color)})`
}

/** Text in a calendar's color on its tint (an event in the grid, its title and time): one of the eleven has its own
 * text shade; an own color is held dark in the light look and light in the dark one (`--own-text-min/max`), so it
 * reads on its tint whatever the color, also yellow or near black. */
// eslint-disable-next-line react-refresh/only-export-components
export function textColorVar(color: CalendarColor | string): string {
  if (isOwnColor(color)) return `oklch(from ${color} clamp(var(--own-text-min), l, var(--own-text-max)) c h)`
  return `var(--color-calt-${paletteName(color)})`
}

export function Swatch({ color, hollow = false, size = 12, icon }: { color: CalendarColor | string; hollow?: boolean; size?: number; icon?: string | null }) {
  const drawn = symbolIcon(icon)
  if (drawn) {
    // A hidden calendar's symbol stays, only paler: the square's empty edge has no counterpart in a line drawing.
    const props = {
      'aria-hidden': true,
      className: 'shrink-0',
      style: { width: size + 2, height: size + 2, color: colorVar(color), opacity: hollow ? 0.45 : 1 },
      strokeWidth: 2.2,
      'data-color': color,
      'data-symbol': icon,
    }
    return createElement(drawn, props as Parameters<typeof createElement>[1])
  }
  const style: CSSProperties = {
    width: size,
    height: size,
    borderRadius: 3,
    border: `2px solid ${colorVar(color)}`,
    background: hollow ? 'transparent' : colorVar(color),
  }
  return <span aria-hidden className="inline-block shrink-0" style={style} data-color={color} />
}
