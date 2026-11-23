/* eslint-disable react-refresh/only-export-components */
/**
 * The search (TIME-22, drawing "T22-Suche"), built like nexnotes' search: the field "Suchen" in the sidebar with
 * Ctrl/⌘ K (also Ctrl/⌘ Shift F, and `/` while nothing is being written), on a phone "Suchen" in the bar at the bottom,
 * and the page `/search`.
 *
 * - Hits come while typing, after a short pause; an older answer that arrives late is dropped (AC-1).
 * - Above them the count with the collection and the fields (AC-5); then "Künftig", the next on top, and "Vergangen",
 *   the latest on top (AC-2), each with date, color dot, title and calendar; a series once, "nächster Termin am …"
 *   (EC-1), a declined invitation marked (EC-15). Titles are text, never markup (EC-5).
 * - Nothing found: the dashed box "Nichts zu „…“" with where it looked, and "In allen Kalendern suchen" when a
 *   collection is chosen (AC-6).
 * - A hit opens its event and the view jumps to its day (AC-3).
 * - 100 hits at a time, the next ones when the end of the list comes into view (EC-11).
 * - Keys: arrow down from the field into the list, Enter opens the first hit, Escape empties the field and then leaves.
 * - Without a connection in the installed app it looks in the four weeks kept and says so (TIME-28 EC-11).
 */
import { Search, SearchX } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { NavLink, useNavigate, useSearchParams } from 'react-router-dom'

import { useDisplay } from '../../lib/useDisplay'
import { useTitle } from '../../lib/title'
import { usePanel } from '../calendars/store'
import { clockText, wallOf } from '../calendars/time'
import { useShown } from '../collections/store'
import { useKindName, useStamp } from '../sources/store'
import { Dot, useCalendarMap } from '../views/bits'
import { setViewDay, useNow } from '../views/state'
import { searchApi, type Found, type Hit } from './api'
import { noteFailure, useOffline } from '../offline/connection'
import { keptSearch } from '../offline/kept'
import { useKept } from '../offline/snapshot'

export const SEARCH_PATH = '/search'
/** Waiting after a keystroke before asking, so fast typing asks once. */
export const TYPE_DELAY_MS = 120
/** Sent on `window` to put the cursor into the field while the page is open. */
const FOCUS_EVENT = 'nextime:focus-search'

function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
}

function writing(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element || typeof element.closest !== 'function') return false
  return Boolean(element.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]'))
}

/** The search's keys, everywhere in the app (installed by the shell): Ctrl/⌘ + K as in the other nex apps,
 * Ctrl/⌘ + Shift + F, and `/` while no text is being written. On the search page they put the cursor into the field. */
export function useSearchKeys(): void {
  const navigate = useNavigate()
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey) return
      const command = event.ctrlKey || event.metaKey
      const lower = event.key.toLowerCase()
      const findAll = command && event.shiftKey && lower === 'f'
      const family = command && !event.shiftKey && lower === 'k'
      const slash = event.key === '/' && !command && !writing(event.target)
      if (!findAll && !family && !slash) return
      event.preventDefault()
      if (window.location.pathname === SEARCH_PATH) window.dispatchEvent(new Event(FOCUS_EVENT))
      else navigate(SEARCH_PATH)
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [navigate])
}

/** The field "Suchen" in the sidebar, right under "Neuer Termin". */
export function SearchEntry({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation()
  return (
    <NavLink
      to={SEARCH_PATH}
      onClick={onNavigate}
      data-testid="sidebar-search"
      aria-keyshortcuts={isMac() ? 'Meta+K Meta+Shift+F' : 'Control+K Control+Shift+F'}
      className={({ isActive }) =>
        'mb-2 flex h-9 items-center gap-2 rounded-[9px] border px-2.5 text-[13.5px] ' +
        (isActive ? 'border-accent-500 text-accent-400' : 'border-ink-700 text-mist-500 hover:bg-ink-800 hover:text-mist-100')
      }
    >
      <Search className="h-[15px] w-[15px]" strokeWidth={2} aria-hidden />
      <span className="flex-1">{t('search.field')}</span>
      <kbd className="hidden md:inline" data-testid="search-key">
        {isMac() ? '⌘ K' : t('search.key')}
      </kbd>
    </NavLink>
  )
}

/** "heute, 16:30", "Di, 15. Dez", with the year when it is another one (AC-2, EC-12: the device's zone). */
export function hitDate(hit: Hit, zone: string, today: string, language: string, twelve: boolean, todayWord = ''): { date: string; text: string } {
  const date = hit.all_day ? hit.start : wallOf(Date.parse(hit.start), zone).date
  const [year, month, day] = date.split('-').map(Number)
  const at = new Date(Date.UTC(year, month - 1, day))
  const clean = (text: string) => text.replace(/\.$/, '')
  const weekday = clean(new Intl.DateTimeFormat(language, { weekday: 'short', timeZone: 'UTC' }).format(at))
  const dayMonth = new Intl.DateTimeFormat(language, { day: 'numeric', month: 'short', ...(date.slice(0, 4) !== today.slice(0, 4) ? { year: 'numeric' } : {}), timeZone: 'UTC' })
    .formatToParts(at)
    .map((part) => (part.type === 'month' ? clean(part.value) : part.value))
    .join('')
  const time = hit.all_day ? '' : clockText(wallOf(Date.parse(hit.start), zone).time, language, twelve)
  if (date === today && !hit.recurring && todayWord) return { date, text: time ? `${todayWord}, ${time}` : todayWord }
  return { date, text: `${weekday}, ${dayMonth}` }
}

/** What is shown, and the "now" of its first page: further pages ask with the same one, so that no hit slips from
 * "Künftig" to "Vergangen" between two pages (review of TIME-22, finding 2). */
type Shown = { key: string; found: Found; items: Hit[]; now: number; kept?: boolean }

export function SearchPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { open } = usePanel()
  const { zone, twelve } = useDisplay()
  const language = i18n.language
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const everywhere = params.get('all') === '1'
  const shown = useShown()
  const calendars = useCalendarMap()
  const kindName = useKindName()
  const stamp = useStamp()
  const now = useNow()
  const today = wallOf(now, zone).date
  useTitle(t('search.title'))
  // Where it looks: the collection chosen on the device, or everything ("In allen Kalendern suchen", AC-6).
  const only = everywhere ? null : shown.calendars
  const chosen = shown.only ? shown.only.name : (shown.collection?.name ?? null)
  const where = everywhere ? null : chosen
  const key = JSON.stringify([q.trim(), only])
  const offline = useOffline()
  const { snapshot } = useKept()
  const [current, setCurrent] = useState<Shown | null>(null)
  const [failed, setFailed] = useState(false)
  const [more, setMore] = useState(false)
  const asked = useRef(0)
  const field = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)

  const change = (next: { q?: string; all?: boolean }) =>
    setParams(
      () => {
        const written = new URLSearchParams()
        const text = next.q ?? q
        if (text) written.set('q', text)
        if (next.all ?? everywhere) written.set('all', '1')
        return written
      },
      { replace: true },
    )

  useEffect(() => {
    const words = q.trim()
    const ticket = ++asked.current
    if (!words) {
      setCurrent(null)
      setFailed(false)
      return
    }
    const timer = window.setTimeout(() => {
      const asking = Date.now()
      if (offline && snapshot) {
        const found = keptSearch(snapshot, words, only, asking, wallOf(asking, zone).date)
        setFailed(false)
        setCurrent({ key, found, items: found.items, now: asking, kept: true })
        return
      }
      searchApi
        .find({ q: words, calendars: only, now: asking, zone, offset: 0 })
        .then((found) => {
          if (ticket !== asked.current) return
          setFailed(false)
          setCurrent({ key, found, items: found.items, now: asking })
        })
        .catch((error) => {
          if (ticket !== asked.current) return
          noteFailure(error)
          setFailed(true)
          setCurrent(null)
        })
    }, TYPE_DELAY_MS)
    return () => window.clearTimeout(timer)
    // `key` stands for the question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, zone, offline, snapshot])

  const shownNow = current && current.key === key ? current : null

  const loadMore = useCallback(() => {
    if (!shownNow || more || shownNow.items.length >= shownNow.found.total) return
    const ticket = asked.current
    setMore(true)
    searchApi
      .find({ q: q.trim(), calendars: only, now: shownNow.now, zone, offset: shownNow.items.length })
      .then((found) => {
        if (ticket !== asked.current) return
        setCurrent((before) => {
          if (!before || before.key !== key) return before
          const known = new Set(before.items.map((hit) => `${hit.id}-${hit.calendar_id}`))
          return { ...before, items: [...before.items, ...found.items.filter((hit) => !known.has(`${hit.id}-${hit.calendar_id}`))] }
        })
      })
      .catch(() => undefined)
      .finally(() => setMore(false))
  }, [shownNow, more, q, only, zone, key])

  // The next page when the end of the list comes into view.
  useEffect(() => {
    const end = sentinel.current
    if (!end || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) loadMore()
    }, { rootMargin: '400px' })
    observer.observe(end)
    return () => observer.disconnect()
  }, [loadMore])

  useEffect(() => {
    const focus = () => {
      field.current?.focus()
      field.current?.select()
    }
    window.addEventListener(FOCUS_EVENT, focus)
    return () => window.removeEventListener(FOCUS_EVENT, focus)
  }, [])

  const leave = () => {
    const index = typeof window.history.state?.idx === 'number' ? window.history.state.idx : 0
    if (index > 0) navigate(-1)
    else navigate('/')
  }

  /** The event opens and the view jumps to its day (AC-3). */
  const show = (hit: Hit) => {
    setViewDay(hitDate(hit, zone, today, language, twelve).date)
    navigate('/day')
    open({ kind: 'open', id: hit.id, occurrence: hit.occurrence })
  }

  const onFieldKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      const first = list.current?.querySelector<HTMLButtonElement>('button[data-hit]')
      if (first) {
        event.preventDefault()
        first.focus()
      }
    } else if (event.key === 'Enter') {
      const first = shownNow?.items[0]
      if (first) {
        event.preventDefault()
        show(first)
      }
    } else if (event.key === 'Escape') {
      event.preventDefault()
      if (q) change({ q: '' })
      else leave()
    }
  }

  const onListKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>('button[data-hit]') ?? [])]
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (index < 0) return
    event.preventDefault()
    if (event.key === 'ArrowUp' && index === 0) field.current?.focus()
    else buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus()
  }

  const nothing = shownNow !== null && shownNow.found.total === 0
  const coming = shownNow?.items.filter((hit) => hit.coming) ?? []
  const past = shownNow?.items.filter((hit) => !hit.coming) ?? []
  const behind = shownNow?.found.behind ?? []
  // "5 Treffer in „Alles“" (AC-5); after "In allen Kalendern suchen": "in allen Kalendern".
  const place = everywhere ? t('search.inAll') : t('search.inCollection', { name: chosen ?? t('collections.all') })

  const row = (hit: Hit) => {
    const calendar = calendars.get(hit.calendar_id)
    const when = hitDate(hit, zone, today, language, twelve, t('search.today')).text
    return (
      <button
        key={`${hit.id}-${hit.calendar_id}`}
        type="button"
        data-hit
        className="flex w-full items-center gap-3 rounded-[10px] border-b border-ink-800 px-0 py-3 text-left text-sm hover:bg-ink-850 focus:outline-none focus-visible:bg-ink-850 md:px-3"
        onClick={() => show(hit)}
        data-testid="search-hit"
      >
        <span className="w-[8.5rem] shrink-0 text-xs text-mist-500 sm:w-40" data-testid="search-hit-date">
          {hit.recurring ? (hit.coming ? t('search.next', { date: when }) : t('search.last', { date: when })) : when}
        </span>
        <Dot color={calendar?.color ?? 'gray'} />
        <span className="nt-wrap min-w-0 flex-1 text-mist-100" data-testid="search-hit-title">
          {hit.title}
          {hit.declined && (
            <>
              {' '}
              <span className="inline-block rounded bg-ink-800 px-1.5 text-[10.5px] font-bold whitespace-nowrap text-mist-400" data-testid="search-declined">
                {t('search.declined')}
              </span>
            </>
          )}
        </span>
        <span className="nt-hide-narrow shrink-0 text-xs text-mist-500" data-testid="search-hit-calendar">
          {calendar?.name}
        </span>
      </button>
    )
  }

  return (
    <div className="flex min-h-full flex-col gap-3.5 px-4 py-5 md:px-8 md:py-6" data-testid="search-page">
      <div className="flex max-w-[880px] items-center gap-2.5">
        <label className="flex h-11 flex-1 items-center gap-2.5 rounded-xl border border-edge bg-ink-850 px-3.5 focus-within:border-accent-500 focus-within:shadow-[0_0_0_3px_var(--color-focus-ring)] md:h-12">
          <Search className="h-[18px] w-[18px] shrink-0 text-mist-500" strokeWidth={2} aria-hidden />
          <input
            ref={field}
            type="search"
            autoFocus
            value={q}
            maxLength={500}
            onChange={(event) => change({ q: event.target.value, all: false })}
            onKeyDown={onFieldKey}
            placeholder={t('search.placeholder')}
            aria-label={t('search.field')}
            enterKeyHint="search"
            className="min-w-0 flex-1 bg-transparent text-base text-mist-100 outline-none placeholder:text-mist-600 md:text-[17px] [&::-webkit-search-cancel-button]:hidden"
            data-testid="search-field"
          />
        </label>
        <button type="button" className="h-11 px-1 text-base text-accent-400 lg:hidden" onClick={leave} data-testid="search-done">
          {t('common.done')}
        </button>
      </div>

      <div className="max-w-[880px] space-y-3">
        {!q.trim() && (
          <p className="py-10 text-center text-sm text-mist-500" data-testid="search-hint">
            {t('search.hint')}
          </p>
        )}
        {failed && (
          <p className="py-6 text-sm text-bad-500" role="alert" data-testid="search-failed">
            {t('search.failed')}
          </p>
        )}
        {shownNow && !nothing && (
          <p className="text-[13px] text-mist-500" data-testid="search-count" aria-live="polite">
            {t('search.count', { count: shownNow.found.total, where: place })}
            {behind.map((source) => ` · ${t('search.behind', { name: kindName(source.kind), time: stamp(source.synced_at) })}`).join('')}
            {shownNow.kept && <span data-testid="search-kept"> · {t('offline.searchKept')}</span>}
          </p>
        )}
        {nothing && (
          <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-ink-600 p-6 text-center md:min-h-[420px]" data-testid="search-empty">
            <SearchX className="h-10 w-10 text-accent-400" strokeWidth={1.6} aria-hidden />
            <span className="nt-wrap text-lg font-semibold text-mist-100" data-testid="search-empty-title">
              {t('search.empty.title', { q: q.trim() })}
            </span>
            <span className="text-sm text-mist-500" data-testid="search-empty-where">
              {where === null ? t('search.empty.inAll') : t('search.empty.inCollection', { name: where })}
              {shownNow?.kept && <span data-testid="search-kept"> · {t('offline.searchKept')}</span>}
            </span>
            {where !== null && (
              <button type="button" className="nt-btn nt-btn-accent" onClick={() => change({ all: true })} data-testid="search-everywhere">
                {t('search.empty.everywhere')}
              </button>
            )}
          </div>
        )}
        {shownNow && !nothing && (
          <div ref={list} onKeyDown={onListKey} className="space-y-3">
            {coming.length > 0 && (
              <section aria-labelledby="search-coming" className="space-y-1.5">
                <h2 id="search-coming" className="text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase">
                  {t('search.coming')}
                </h2>
                <div className="flex flex-col" data-testid="search-coming">
                  {coming.map(row)}
                </div>
              </section>
            )}
            {past.length > 0 && (
              <section aria-labelledby="search-past" className="space-y-1.5">
                <h2 id="search-past" className="text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase">
                  {t('search.past')}
                </h2>
                <div className="flex flex-col" data-testid="search-past">
                  {past.map(row)}
                </div>
              </section>
            )}
            <div ref={sentinel} />
            {shownNow.items.length < shownNow.found.total && (
              <div className="flex justify-center pt-1">
                <button type="button" className="nt-btn nt-btn-ghost" onClick={loadMore} disabled={more} data-testid="search-more">
                  {t('search.more', { count: shownNow.found.total - shownNow.items.length })}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
