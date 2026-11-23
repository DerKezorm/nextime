/** Looking up and searching the symbols of `symbols.ts`. */
import type { LucideIcon } from 'lucide-react'

import { SYMBOLS, type SymbolEntry } from './symbols'

const BY_NAME = new Map(SYMBOLS.map((entry) => [entry.name, entry.icon]))

/** The drawing of a symbol; none for an empty or unknown name. */
export function symbolIcon(name: string | null | undefined): LucideIcon | null {
  return BY_NAME.get(name ?? '') ?? null
}

/** Lower case without accents, so that "kuhl" finds "Kühlschrank" and "cafe" finds "Café". */
export function plain(text: string): string {
  return text
    .toLowerCase()
    .replaceAll('ß', 'ss')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
}

/**
 * The symbols a search finds: every word typed starts a word of the symbol, among its name in the page's language
 * (`title`) and its search words in German and English. Nothing typed finds all.
 */
export function findSymbols(query: string, title: (entry: SymbolEntry) => string): SymbolEntry[] {
  const parts = plain(query).split(/[\s-]+/).filter(Boolean)
  if (parts.length === 0) return SYMBOLS
  return SYMBOLS.filter((entry) => {
    const own = plain(`${title(entry)} ${entry.words}`).split(/[\s-]+/)
    return parts.every((part) => own.some((word) => word.startsWith(part)))
  })
}
