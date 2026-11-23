/**
 * "New calendar" and editing one (TIME-4 AC-1, AC-7, EC-3, EC-6, EC-13), "Add calendar" with its seven ways and the
 * count against 20 (AC-6, AC-8, TIME-15 AC-7), and the question before deleting (AC-4).
 */
import { Check } from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { CheckLine } from '../../components/CheckLine'
import { SymbolPicker } from '../../components/SymbolPicker'
import { errorText, problemText } from '../../lib/errors'
import { CALENDAR_COLORS, calendarsApi, newRef, type CalendarColor, type CalendarEntry, type CalendarList, type PaletteColor } from './api'
import type { NewDefaults } from './defaults'
import { NewEventDefaults } from './NewEventDefaults'
import { colorVar, hexOf, isOwnColor, Swatch } from './Swatch'
import { sourcesApi } from '../sources/api'
import { useKindName } from '../sources/store'

const NAME_MAX = 60


/**
 * The eleven colors as a radio group: arrow keys move, each has its name for a screen reader (EC-13). Below, as
 * nexnotes' "Symbol und Farbe": "Eigene Farbe" through the system's color picker or as `#rrggbb` (decided while
 * testing, 2026-11-23); a calendar always has a color, so there is no "Ohne Farbe".
 */
export function ColorChoice({ value, onChange, onWrong }: { value: CalendarColor; onChange: (color: CalendarColor) => void; onWrong?: (wrong: boolean) => void }) {
  const { t } = useTranslation()
  const labelId = useId()
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const own = isOwnColor(value) ? value : null
  const at = CALENDAR_COLORS.indexOf(value as PaletteColor)
  const key = (event: KeyboardEvent) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key]
    if (!step) return
    event.preventDefault()
    const next = CALENDAR_COLORS[((at < 0 ? 0 : at) + step + CALENDAR_COLORS.length) % CALENDAR_COLORS.length]
    onChange(next)
    buttons.current[CALENDAR_COLORS.indexOf(next)]?.focus()
  }
  return (
    <div className="text-sm">
      <span id={labelId} className="nt-legend block">
        {t('calendars.color')}
      </span>
      <div role="radiogroup" aria-labelledby={labelId} className="grid grid-cols-6 gap-2" onKeyDown={key}>
        {CALENDAR_COLORS.map((color, index) => {
          const chosen = value === color
          return (
            <button
              key={color}
              ref={(node) => {
                buttons.current[index] = node
              }}
              type="button"
              role="radio"
              aria-checked={chosen}
              aria-label={t(`calendars.colors.${color}`)}
              title={t(`calendars.colors.${color}`)}
              tabIndex={chosen || (at < 0 && index === 0) ? 0 : -1}
              onClick={() => onChange(color)}
              className={'flex h-8 items-center justify-center rounded-lg ' + (chosen ? 'ring-2 ring-mist-100 ring-offset-2 ring-offset-ink-850' : '')}
              style={{ background: colorVar(color) }}
              data-testid={`color-${color}`}
            >
              {chosen && <Check aria-hidden className="h-4 w-4 text-on-accent" strokeWidth={3} />}
            </button>
          )
        })}
      </div>
      <OwnColor value={own} onChange={onChange} onWrong={onWrong} />
    </div>
  )
}

/** "Eigene Farbe": the system's picker behind a tile, beside it the field `#rrggbb` (as nexnotes' `ColorField`). */
function OwnColor({ value, onChange, onWrong }: { value: `#${string}` | null; onChange: (color: CalendarColor) => void; onWrong?: (wrong: boolean) => void }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(value ?? '')
  const [wrong, setWrongState] = useState(false)
  // The dialog keeps "Sichern" from saving the old color while a wrong one stands in the field (review, 2026-11-23).
  const setWrong = (on: boolean) => {
    setWrongState(on)
    onWrong?.(on)
  }
  const picker = useRef<HTMLInputElement>(null)
  const wrongId = useId()

  useEffect(() => setDraft(value ?? ''), [value])

  // The system's picker reports every move while it is open ("input") and the result when it closes ("change"): the
  // field follows the moves, the choice is the result.
  useEffect(() => {
    const input = picker.current
    if (!input) return
    const done = () => {
      const hex = hexOf(input.value)
      if (hex) {
        setWrong(false)
        onChange(hex)
      }
    }
    input.addEventListener('change', done)
    return () => input.removeEventListener('change', done)
  })

  const typed = () => {
    if (!draft.trim()) return
    const hex = hexOf(draft)
    if (hex) {
      setWrong(false)
      onChange(hex)
    } else setWrong(true)
  }

  return (
    <div className="mt-3" data-testid="own-color">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm text-mist-300">
          <span className={'relative inline-flex rounded-lg ' + (value ? 'ring-2 ring-mist-100 ring-offset-2 ring-offset-ink-850' : '')} data-testid="own-color-tile" data-chosen={value ? 'true' : undefined}>
            <input
              ref={picker}
              type="color"
              value={value ?? '#7a8890'}
              aria-label={t('calendars.ownColor.pick')}
              onChange={(event) => setDraft(event.target.value)}
              className="h-8 w-10 cursor-pointer rounded-lg border border-ink-600 bg-transparent p-0.5"
              data-testid="color-picker"
            />
            {value && <Check aria-hidden className="pointer-events-none absolute inset-0 m-auto h-4 w-4" strokeWidth={3} style={{ color: inkOn(value) }} data-testid="own-color-chosen" />}
          </span>
          {t('calendars.ownColor.label')}
        </label>
        <input
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value)
            setWrong(false)
          }}
          onBlur={typed}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return
            event.preventDefault()
            typed()
          }}
          placeholder="#rrggbb"
          aria-label={t('calendars.ownColor.hex')}
          aria-invalid={wrong || undefined}
          aria-describedby={wrong ? wrongId : undefined}
          maxLength={7}
          spellCheck={false}
          className="nt-field w-28 font-mono"
          data-testid="color-hex"
        />
      </div>
      {wrong && (
        <p id={wrongId} role="alert" className="mt-1 text-xs text-bad-500" data-testid="color-hex-wrong">
          {t('calendars.ownColor.wrong')}
        </p>
      )}
    </div>
  )
}

/** Black or white, whichever reads on the color (the check mark on an own color). */
function inkOn(hex: string): string {
  const [r, g, b] = [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.4 ? '#111111' : '#ffffff'
}

/**
 * The symbol of a calendar (TIME-4, "Kalender-Symbol wie nexnotes"): nexnotes' picker with search, in the calendar's
 * color, beside the color. "Ohne Symbol" keeps the color square.
 */
export function SymbolChoice({ value, color, onChange }: { value: string; color: CalendarColor; onChange: (icon: string) => void }) {
  const { t } = useTranslation()
  return (
    <fieldset className="text-sm" data-testid="calendar-symbol">
      <div className="mb-2 flex items-center justify-between gap-3">
        <legend className="nt-legend">{t('calendars.icon')}</legend>
        <button type="button" className={'text-xs ' + (value ? 'text-accent-400 hover:underline' : 'text-mist-500')} disabled={!value} onClick={() => onChange('')} data-testid="calendar-symbol-none">
          {t('calendars.noIcon')}
        </button>
      </div>
      <SymbolPicker value={value} onChange={onChange} color={colorVar(color)} />
    </fieldset>
  )
}

/** What new events of a calendar get as reminders while the person chose none: the reminder of an own calendar
 * (TIME-7 AC-3), at a whole day "on the day at 09:00" under a day, else "the day before at 18:00". */
// eslint-disable-next-line react-refresh/only-export-components
export function fallbackLeads(calendar: CalendarEntry | undefined, reminder: number, allDay: boolean): number[] {
  if ((calendar && (calendar.right !== 'owner' || calendar.origin)) || !reminder) return []
  return [allDay ? (reminder >= 1440 ? 360 : -540) : reminder]
}

export function CalendarDialog({ calendar, onDone, onDelete, onClose }: { calendar?: CalendarEntry; onDone: (list: CalendarList) => void; onDelete?: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [name, setName] = useState(calendar?.name ?? '')
  const [color, setColor] = useState<CalendarColor>(calendar?.color ?? 'slate')
  const [icon, setIcon] = useState(calendar?.icon ?? '')
  const [background, setBackground] = useState(calendar?.background ?? false)
  // "Highlight its days" is the person's own; as from the start on for the built-in public holidays only.
  const [holiday, setHoliday] = useState(calendar?.holiday ?? false)
  const [standard, setStandard] = useState(calendar?.default ?? false)
  // The calendar's own reminder (TIME-7) stays as it is; the reminders chosen here are the person's (TIME-4 defaults).
  const reminder = calendar?.reminder ?? 0
  const [defaults, setDefaults] = useState<NewDefaults>(calendar?.defaults ?? {})
  const [nameProblem, setNameProblem] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // One id for this dialog: a second click or a retry after a broken connection makes no second calendar (EC-6).
  const ref = useRef(newRef())
  const colorWrong = useRef(false)
  const inFlight = useRef(false)
  const nameId = useId()

  const save = async () => {
    if (inFlight.current) return
    // A wrong "#rrggbb" says so at its field; nothing is saved with the old color meanwhile.
    if (colorWrong.current) return
    const clean = name.trim()
    if (!clean) return setNameProblem(t('calendars.nameMissing'))
    if (clean.length > NAME_MAX) return setNameProblem(t('calendars.nameTooLong', { now: clean.length }))
    setNameProblem(null)
    setProblem(null)
    inFlight.current = true
    setBusy(true)
    try {
      // Only a changed choice goes along, so a calendar left alone keeps following what holds from the start.
      const form = { name: clean, color, icon, background, default: standard, reminder, defaults, ...(holiday !== (calendar?.holiday ?? false) ? { holiday } : {}) }
      onDone(calendar ? await calendarsApi.change(calendar.id, form) : await calendarsApi.create(form, ref.current))
    } catch (error) {
      if (error instanceof ApiError && error.values.field === 'name') setNameProblem(errorText(error.code, error.values))
      else setProblem(error instanceof ApiError ? problemText(error) : errorText('network'))
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  return (
    <Dialog title={calendar ? t('calendars.editTitle', { name: calendar.name }) : t('calendars.newTitle')} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
        data-testid="calendar-dialog"
      >
        <div className="text-sm">
          <label htmlFor={nameId} className="font-medium text-mist-100">
            {t('calendars.name')}
          </label>
          <div className="mt-1 flex items-center gap-2.5">
            {/* What the calendar will look like in the list, as nexnotes' folder dialog shows it. */}
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[9px] border border-ink-700" data-testid="calendar-look">
              <Swatch color={color} icon={icon} size={icon ? 18 : 14} />
            </span>
            <input
              id={nameId}
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={nameProblem ? true : undefined}
              aria-describedby={nameProblem ? `${nameId}-problem` : undefined}
              className={'nt-field ' + (nameProblem ? 'border-bad-500' : '')}
              data-testid="calendar-name-field"
            />
          </div>
          {nameProblem && (
            <span id={`${nameId}-problem`} role="alert" className="mt-1 block text-xs text-bad-500">
              {nameProblem}
            </span>
          )}
        </div>
        <ColorChoice value={color} onChange={setColor} onWrong={(on) => (colorWrong.current = on)} />
        <SymbolChoice value={icon} color={color} onChange={setIcon} />
        <CheckLine label={t('calendars.background')} hint={t('calendars.backgroundHint')} checked={background} onChange={setBackground} />
        {background && (
          <CheckLine label={t('calendars.highlight')} hint={t('calendars.highlightHint')} checked={holiday} onChange={setHoliday} testId="calendar-highlight" />
        )}
        <CheckLine label={t('calendars.standard')} checked={standard} onChange={setStandard} />
        <NewEventDefaults value={defaults} fallbackReminders={(allDay) => fallbackLeads(calendar, reminder, allDay)} onChange={setDefaults} />
        <p className="text-xs text-mist-500">{t('calendars.lives')}</p>
        {problem && (
          <p role="alert" className="rounded-[9px] border border-bad-500/30 bg-bad-500/10 px-3 py-2 text-sm text-bad-500">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {calendar && onDelete && (
            <button type="button" className="nt-btn nt-btn-danger mr-auto" onClick={onDelete}>
              {t('calendars.deleteButton')}
            </button>
          )}
          <button type="button" className="nt-btn nt-btn-ghost ml-auto" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={busy} data-testid="calendar-save">
            {calendar ? t('calendars.saveButton') : t('calendars.create')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/** A way of "Add calendar"; `googleRead` is Google's read-only way while Google is not set up (TIME-16 AC-4). */
export type Way = 'own' | 'icloud' | 'google' | 'googleRead' | 'caldav' | 'subscription' | 'holidays' | 'file'

/**
 * "Add calendar" (TIME-15 AC-7): the seven ways with a sentence each and "lesen und schreiben", "nur lesen" or
 * "einmalig", and the count of all calendars of the account against 20 (TIME-4 AC-6). Google with a set-up of the
 * operator connects (TIME-16 AC-2); without one it offers the read-only way (AC-4); switched off, the way is not there
 * (AC-5). "Datei laden" leads to "Mitnehmen und umziehen" (TIME-24 AC-5).
 */
export function AddCalendarDialog({ count, limit, google, onWay, onClose }: {
  count: number
  limit: number
  /** Whether Google is set up and on; `null` while that is not known yet. */
  google: { ready: boolean; configured: boolean } | null
  onWay: (way: Way) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  // While it is not known yet whether Google is set up, the way stands there, not yet to choose.
  const googleWay: { key: Way; tag: string; ready: boolean } | null = !google
    ? { key: 'google', tag: 'readWrite', ready: false }
    : !google.configured
      ? { key: 'googleRead', tag: 'readOnly', ready: true }
      : google.ready
        ? { key: 'google', tag: 'readWrite', ready: true }
        : null
  const ways: { key: Way; label: string; tag: string; ready: boolean }[] = [
    { key: 'own', label: 'own', tag: 'readWrite', ready: true },
    { key: 'icloud', label: 'icloud', tag: 'readWrite', ready: true },
    ...(googleWay ? [{ key: googleWay.key, label: 'google', tag: googleWay.tag, ready: googleWay.ready }] : []),
    { key: 'caldav', label: 'caldav', tag: 'readWrite', ready: true },
    { key: 'subscription', label: 'subscription', tag: 'readOnly', ready: true },
    { key: 'holidays', label: 'holidays', tag: 'readOnly', ready: true },
    { key: 'file', label: 'file', tag: 'once', ready: true },
  ]
  return (
    <Dialog title={t('calendars.addTitle')} onClose={onClose} wide>
      <div className="space-y-4" data-testid="add-calendar-dialog">
        <div className="grid gap-2 sm:grid-cols-2">
          {ways.map((way) => (
            <button
              key={way.key}
              type="button"
              disabled={!way.ready}
              onClick={() => onWay(way.key)}
              className="flex min-w-0 flex-col items-start gap-1 rounded-[12px] border border-ink-700 px-3.5 py-3 text-left hover:bg-ink-800 disabled:cursor-not-allowed disabled:opacity-60"
              data-testid={`way-${way.key}`}
            >
              <strong className="text-[14.5px] font-semibold text-mist-100">{t(`calendars.ways.${way.label}.title`)}</strong>
              <span className="text-[12.5px] leading-snug text-mist-500">{t(way.key === 'googleRead' ? 'calendars.ways.google.readText' : `calendars.ways.${way.label}.text`)}</span>
              <span className={'nt-mark ' + (way.tag === 'readWrite' ? '' : 'nt-mark-gray')} data-testid="tag-of-way">
                {t(`calendars.tags.${way.tag}`)}
              </span>
            </button>
          ))}
        </div>
        {count > limit && (
          <p className="text-xs text-mist-500" data-testid="calendar-count">
            {t('calendars.countOver', { count })}
          </p>
        )}
      </div>
    </Dialog>
  )
}

export function DeleteCalendarDialog({ calendar, onDone, onClose }: { calendar: CalendarEntry; onDone: (list: CalendarList) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const remove = async () => {
    setBusy(true)
    try {
      onDone(await calendarsApi.remove(calendar.id))
    } catch (error) {
      setProblem(problemText(error))
      setBusy(false)
    }
  }
  return (
    <Dialog title={t('calendars.deleteTitle', { name: calendar.name })} onClose={onClose}>
      <div className="space-y-4" data-testid="delete-calendar-dialog">
        <p className="text-sm text-mist-200">{t('calendars.deleteText', { count: calendar.events })}</p>
        {problem && (
          <p role="alert" className="text-sm text-bad-500">
            {problem}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="nt-btn nt-btn-danger" disabled={busy} onClick={() => void remove()}>
            {t('calendars.deleteConfirm')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

/**
 * "Aus nextime entfernen" for a calendar of iCloud, Google or a CalDAV server (asked for while testing, 2026-11-23):
 * the same as leaving it out in "Kalender wählen"; at the source it stays, the way back is named. Changes still
 * waiting are counted first, as there.
 */
export function LeaveCalendarDialog({ calendar, onDone, onClose }: { calendar: CalendarEntry; onDone: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const kindName = useKindName()
  const [busy, setBusy] = useState(false)
  const [waiting, setWaiting] = useState(0)
  const [problem, setProblem] = useState<string | null>(null)
  const origin = calendar.origin!
  const kind = origin.kind === 'caldav' ? origin.label || kindName('caldav') : kindName(origin.kind)
  const leave = async () => {
    setBusy(true)
    setProblem(null)
    try {
      await sourcesApi.leave(origin.id, calendar.id, waiting > 0)
      onDone()
    } catch (error) {
      if (error instanceof ApiError && error.code === 'changes_waiting') setWaiting(Number(error.values.count ?? 1))
      else setProblem(problemText(error))
      setBusy(false)
    }
  }
  return (
    <Dialog title={t('calendars.leave.title', { name: calendar.name })} onClose={onClose}>
      <div className="space-y-4" data-testid="leave-calendar-dialog">
        <p className="text-sm text-mist-200">{t('calendars.leave.text', { kind })}</p>
        {waiting > 0 && (
          <p role="alert" className="text-sm text-warn-500" data-testid="leave-calendar-waiting">
            {t('calendars.leave.waiting', { count: waiting, kind })}
          </p>
        )}
        {problem && (
          <p role="alert" className="text-sm text-bad-500">
            {problem}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="nt-btn nt-btn-danger" disabled={busy} onClick={() => void leave()} data-testid="leave-calendar-confirm">
            {t('calendars.leave.confirm')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}
