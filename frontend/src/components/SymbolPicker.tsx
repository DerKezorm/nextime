/**
 * The symbol picker of calendars, as nexnotes has it for folders and notes: a search over the names in German and English, otherwise all symbols in
 * their groups. One stop in the tab order: the arrow keys move inside the grid (up and down by a row), Home and End go
 * to the first and last, Enter in the search takes the only match. The chosen one stays marked, also when the search
 * hides it.
 */
import { Search } from 'lucide-react'
import { useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { findSymbols } from '../lib/symbolLook'
import { SYMBOL_GROUPS, type SymbolEntry } from '../lib/symbols'

export function SymbolPicker({ value, onChange, color }: { value: string; onChange: (name: string) => void; color?: string }) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const grid = useRef<HTMLDivElement>(null)
  const title = (entry: SymbolEntry) => t(`symbols.names.${entry.name}`)
  const found = useMemo(() => findSymbols(query, (entry) => t(`symbols.names.${entry.name}`)), [query, t])
  const searching = query.trim() !== ''
  // The one stop in the tab order: the chosen symbol if it shows, else the first.
  const stop = found.some((entry) => entry.name === value) ? value : found[0]?.name

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const buttons = [...(grid.current?.querySelectorAll<HTMLButtonElement>('[role="radio"]') ?? [])]
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (at < 0) return
    const here = buttons[at].getBoundingClientRect()
    // Up and down: the nearest symbol in the row above or below, by position (the groups' rows differ in length).
    const vertical = (down: boolean) => {
      const rows = buttons.filter((button) => {
        const box = button.getBoundingClientRect()
        return down ? box.top > here.top + 1 : box.top < here.top - 1
      })
      if (rows.length === 0) return at
      const row = down ? Math.min(...rows.map((b) => b.getBoundingClientRect().top)) : Math.max(...rows.map((b) => b.getBoundingClientRect().top))
      const inRow = rows.filter((button) => Math.abs(button.getBoundingClientRect().top - row) < 1)
      const best = inRow.reduce((a, b) => (Math.abs(b.getBoundingClientRect().left - here.left) < Math.abs(a.getBoundingClientRect().left - here.left) ? b : a))
      return buttons.indexOf(best)
    }
    const next =
      event.key === 'ArrowRight' ? Math.min(at + 1, buttons.length - 1)
      : event.key === 'ArrowLeft' ? Math.max(at - 1, 0)
      : event.key === 'ArrowDown' ? vertical(true)
      : event.key === 'ArrowUp' ? vertical(false)
      : event.key === 'Home' ? 0
      : event.key === 'End' ? buttons.length - 1
      : -1
    if (next < 0) return
    event.preventDefault()
    buttons[next].focus()
  }

  const button = (entry: SymbolEntry) => {
    const chosen = entry.name === value
    return (
      <button
        key={entry.name}
        type="button"
        role="radio"
        aria-checked={chosen}
        aria-label={title(entry)}
        title={title(entry)}
        tabIndex={entry.name === stop ? 0 : -1}
        onClick={() => onChange(entry.name)}
        data-symbol={entry.name}
        className={
          'grid h-9 w-full place-items-center rounded-lg outline-offset-2 ' +
          (chosen ? 'bg-[var(--color-accent-wash)] ring-2 ring-accent-500' : 'text-mist-300 hover:bg-ink-800')
        }
        style={chosen && color ? { color } : undefined}
      >
        <entry.icon aria-hidden className="h-[18px] w-[18px]" strokeWidth={2} />
      </button>
    )
  }
  const cells = 'grid grid-cols-[repeat(auto-fill,minmax(2.25rem,1fr))] gap-1.5'

  return (
    <div>
      <label className="relative mb-2 block">
        <Search className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-mist-500" aria-hidden />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              // Enter takes the only match instead of sending the form.
              event.preventDefault()
              if (found.length === 1) onChange(found[0].name)
            }
          }}
          placeholder={t('symbols.search')}
          aria-label={t('symbols.search')}
          className="nt-field pl-8"
          data-testid="symbol-search"
        />
      </label>
      <div ref={grid} className="nt-scroll h-60 overflow-y-auto p-1.5" role="radiogroup" aria-label={t('calendars.icon')} onKeyDown={move} data-testid="symbol-grid">
        {searching ? (
          found.length > 0 ? (
            <div className={cells}>{found.map(button)}</div>
          ) : (
            <p className="py-3 text-sm text-mist-500">{t('symbols.none')}</p>
          )
        ) : (
          SYMBOL_GROUPS.map((group) => (
            <div key={group} className="mb-3 last:mb-0">
              <p className="mb-1.5 text-[11px] font-semibold tracking-wide text-mist-500 uppercase">{t(`symbols.groups.${group}`)}</p>
              <div className={cells}>{found.filter((entry) => entry.group === group).map(button)}</div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
