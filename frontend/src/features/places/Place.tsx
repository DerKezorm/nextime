/**
 * The place of an event (TIME-8, T8-Ort): the field with suggestions while typing (address, below it the place's name;
 * last „„…“ als Text übernehmen“; the sentence about the free service, AC-1, AC-5, EC-5), and the place as the event
 * shows it: the small map with its marker, "© OpenStreetMap" and "In Karten öffnen" (AC-2, AC-3), a web address as a
 * link (AC-7), free text as text that searches in the maps app (EC-2). Only answers to what was typed last are shown
 * (EC-8); a service that does not answer leaves the field as it is (EC-3). Switched off by the operator: no suggestions,
 * no sentence, no map (AC-6).
 */
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { isWebAddress, looksLikeAddress, mapsLink, mapUrl, placesApi, type Point, type Suggestion } from './api'

/** How long the field waits after the last key before it asks, in ms. */
export const ASK_AFTER_MS = 300

export function PlaceField({
  value,
  onChange,
  enabled,
  label,
  problem,
}: {
  value: string
  onChange: (value: string, point: Point | null) => void
  enabled: boolean
  label: string
  problem?: string
}) {
  const { t } = useTranslation()
  const ids = { field: useId(), list: useId() }
  const [open, setOpen] = useState(false)
  const [found, setFound] = useState<Suggestion[]>([])
  const [typed, setTyped] = useState('')
  const asked = useRef(0)
  const box = useRef<HTMLDivElement>(null)

  // Ask after a pause; an answer counts only while it belongs to the last question (EC-8).
  useEffect(() => {
    const text = typed.trim()
    if (!enabled || text.length < 3) return
    const number = ++asked.current
    const timer = setTimeout(() => {
      placesApi.suggest(text).then(
        (answer) => {
          if (number === asked.current) setFound(answer.suggestions)
        },
        () => {
          if (number === asked.current) setFound([])
        },
      )
    }, ASK_AFTER_MS)
    return () => clearTimeout(timer)
  }, [typed, enabled])

  const type = (text: string) => {
    onChange(text, null)
    setTyped(text)
    if (text.trim().length < 3) {
      asked.current++
      setFound([])
    }
    setOpen(enabled && text.trim().length >= 3)
  }
  const choose = (text: string, point: Point | null) => {
    onChange(text, point)
    asked.current++
    setOpen(false)
    setFound([])
  }
  const options = () => Array.from(box.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? [])
  const keys = (event: KeyboardEvent) => {
    if (!open) return
    const all = options()
    const at = all.indexOf(document.activeElement as HTMLElement)
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      all[Math.min(all.length - 1, at + 1)]?.focus()
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      if (at <= 0) document.getElementById(ids.field)?.focus()
      else all[at - 1]?.focus()
    } else if (event.key === 'Escape') {
      event.stopPropagation()
      setOpen(false)
      document.getElementById(ids.field)?.focus()
    }
  }
  const text = value.trim()
  return (
    <div
      ref={box}
      className="relative text-sm"
      onKeyDown={keys}
      onBlur={(event) => {
        if (!box.current?.contains(event.relatedTarget as Node | null)) setOpen(false)
      }}
    >
      <label htmlFor={ids.field} className="font-medium text-mist-100">
        {label}
      </label>
      <input
        id={ids.field}
        value={value}
        onChange={(event) => type(event.target.value)}
        autoComplete="off"
        role={enabled ? 'combobox' : undefined}
        aria-autocomplete={enabled ? 'list' : undefined}
        aria-expanded={enabled ? open : undefined}
        aria-controls={enabled && open ? ids.list : undefined}
        aria-invalid={problem ? true : undefined}
        className={'nt-field mt-1 ' + (problem ? 'border-bad-500' : '')}
        data-testid="field-location"
      />
      {problem && (
        <span role="alert" className="mt-1 block text-xs text-bad-500">
          {problem}
        </span>
      )}
      {open && text.length >= 3 && (
        <div className="absolute right-0 left-0 z-30 mt-1 rounded-xl border border-ink-700 bg-ink-850 p-1 shadow-xl shadow-black/30" data-testid="place-suggestions">
          <div id={ids.list} role="listbox" aria-label={t('places.suggestions')}>
            {found.map((each) => (
              <button
                key={`${each.value}-${each.lat}-${each.lon}`}
                type="button"
                role="option"
                aria-selected={false}
                className="nt-menu-item flex-col items-start gap-0"
                onClick={() => choose(each.value, { lat: each.lat, lon: each.lon })}
                data-testid="place-suggestion"
              >
                <span className="text-mist-100">{each.address}</span>
                {each.name && <span className="text-xs text-mist-500">{each.name}</span>}
              </button>
            ))}
            <button type="button" role="option" aria-selected={false} className="nt-menu-item" onClick={() => choose(text, null)} data-testid="place-as-text">
              {t('places.asText', { text })}
            </button>
          </div>
          <p className="px-2.5 py-2 text-xs text-mist-500" data-testid="place-hint">
            {t('places.hint')}
          </p>
        </div>
      )}
    </div>
  )
}

/**
 * The place as the event shows it. `point` is where a chosen place lies; without one, a place that looks like an
 * address is looked up (`lookup`) while the operator allows it. A web address is a link (AC-7); markup and addresses
 * like `javascript:` stay text and only ever go to the maps app as a search (EC-7).
 */
export function PlaceView({ location, point, enabled, lookup = true, withText = true }: { location: string; point: Point | null; enabled: boolean; lookup?: boolean; withText?: boolean }) {
  const { t } = useTranslation()
  const text = location.trim()
  const [found, setFound] = useState<Point | null>(null)
  const [broken, setBroken] = useState(false)
  const web = isWebAddress(text)
  useEffect(() => {
    setFound(null)
    setBroken(false)
    if (!enabled || !lookup || point || web || !looksLikeAddress(text)) return
    let live = true
    placesApi.lookup(text).then(
      (answer) => live && setFound(answer.place),
      () => undefined,
    )
    return () => {
      live = false
    }
  }, [text, point, enabled, lookup, web])
  if (!text) return null
  if (web)
    return (
      <a href={text} target="_blank" rel="noopener noreferrer" className="block text-sm break-all text-accent-400 hover:underline" data-testid="place-link">
        {text}
      </a>
    )
  const where = point ?? found
  const link = mapsLink(text, where)
  return (
    <div className="space-y-1.5" data-testid="place-view">
      {withText && (
        <a href={link} target="_blank" rel="noopener noreferrer" className="block text-sm break-words whitespace-pre-wrap text-mist-200 hover:underline" data-testid="place-text">
          {text}
        </a>
      )}
      {enabled && where && !broken && (
        <div className="overflow-hidden rounded-[12px] border border-ink-700">
          <img src={mapUrl(where)} alt={t('places.mapOf', { place: text })} className="block h-auto w-full" onError={() => setBroken(true)} data-testid="place-map" />
          <span className="flex items-center justify-between gap-2 bg-ink-900 px-2.5 py-1 text-[11px] text-mist-500">
            <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer" className="hover:underline" data-testid="place-copyright">
              {t('places.copyright')}
            </a>
          </span>
        </div>
      )}
      <a href={link} target="_blank" rel="noopener noreferrer" className="nt-btn nt-btn-quiet text-sm" data-testid="place-open">
        {t('places.open')}
      </a>
    </div>
  )
}
