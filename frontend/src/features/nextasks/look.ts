/**
 * Symbols and colours of nextasks' spaces and lists, drawn as nextasks draws them (its `lib/look.ts`): the names it
 * stores, an unknown name as `list`, its own list colours, an own colour `#rrggbb` held readable in both looks.
 */
import { List, ListChecks, type LucideIcon } from 'lucide-react'

import { colorVar } from '../calendars/Swatch'
import { SYMBOLS } from './symbols'

const BY_NAME = new Map(SYMBOLS.map((entry) => [entry.name, entry.icon]))

/** nextasks' own list colours, by the names of its data model. */
const HEX: Record<string, string> = {
  red: '#d9534f',
  orange: '#dd7a2e',
  yellow: '#d39b12',
  green: '#4f9e3f',
  teal: '#22a08a',
  blue: '#3a84d0',
  indigo: '#5b68cc',
  purple: '#8a59c0',
  pink: '#d65a7c',
  brown: '#9a7550',
  gray: '#7a8890',
}

/** The symbol nextasks stores; without one, the symbol of tasks. */
export function symbolOf(name: string | null | undefined): LucideIcon {
  if (!name) return ListChecks
  return BY_NAME.get(name) ?? List
}

/** The colour nextasks stores, as a CSS colour; `null` without one. */
export function colorOfTask(name: string | null | undefined): string | null {
  if (!name) return null
  if (/^#[0-9a-fA-F]{6}$/.test(name)) return colorVar(name)
  return HEX[name] ?? HEX.blue
}
