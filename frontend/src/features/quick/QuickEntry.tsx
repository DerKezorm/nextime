/* eslint-disable react-refresh/only-export-components */
/**
 * The field "Schnell eintragen" on top of "Heute" (TIME-10, TIME-11 AC-12): while typing, the preview below says what
 * nextime understood (AC-1) in the account's clock (AC-7); Enter saves (AC-2), "Mehr …" opens the side panel with all
 * of it. Without a date, or with an end before the start, Enter opens the side panel instead (EC-1, EC-9); a title too
 * long is said and not saved (EC-4). The same text sent twice (a second Enter, a retry after the connection broke)
 * makes one event (EC-10, EC-11): it carries the same reference until it was saved.
 */
import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { problemText } from '../../lib/errors'
import { showNotice } from '../../lib/notice'
import { useDisplay } from '../../lib/useDisplay'
import { newRef, eventsApi, type CalendarEntry, type EventFields } from '../calendars/api'
import { ruleSentence } from '../calendars/rule'
import { colorVar } from '../calendars/Swatch'
import { announce, useCalendars, usePanel } from '../calendars/store'
import { clockText } from '../calendars/time'
import { suggestion, useShown } from '../collections/store'
import { defaultsOf, remindersFor, ruleOf } from '../calendars/defaults'
import { useNarrow, useNow } from '../views/state'
import { useOffline } from '../offline/connection'
import { TITLE_MAX, understand, type Understood } from './understand'

/** An event understood, with what the text itself said (``typed``) apart from what the calendar's defaults filled in. */
type Event = Extract<Understood, { kind: 'event' }> & { typed?: { length: boolean; rule: boolean } }

/** "Fr, 27. November" or "Fri, November 27"; with the year when it is not this one. */
export function quickDay(date: string, language: string, thisYear: string): string {
  const [year, month, day] = date.split('-').map(Number)
  const at = new Date(Date.UTC(year, month - 1, day))
  const weekday = new Intl.DateTimeFormat(language, { weekday: 'short', timeZone: 'UTC' }).format(at).replace(/\.$/, '')
  const rest = new Intl.DateTimeFormat(language, { day: 'numeric', month: 'long', ...(date.slice(0, 4) !== thisYear ? { year: 'numeric' } : {}), timeZone: 'UTC' }).format(at)
  return `${weekday}, ${rest}`
}

export function QuickEntry() {
  const { t, i18n } = useTranslation()
  // Without a connection nothing is entered (TIME-28 AC-3).
  const offline = useOffline()
  const { zone, twelve } = useDisplay()
  const language = i18n.language
  const { list } = useCalendars()
  const shown = useShown()
  const { open } = usePanel()
  const now = useNow()
  const narrow = useNarrow()
  const [text, setText] = useState('')
  /** Why the last save did not work: the connection, or the server's own sentence. */
  const [failed, setFailed] = useState<string | null>(null)
  const busy = useRef(false)
  const ref = useRef(newRef())

  const calendars = useMemo(() => list?.calendars ?? [], [list])
  // Without `#Name`: the calendar the collection suggests (AC-5, TIME-13 AC-4, AC-13).
  const suggested = suggestion(list, shown)
  const fallback = suggested.id
  /** Understood, with the defaults of its calendar for what the text left open (TIME-4): the length when none was
   * typed, the repeat when none was typed. "2h" or "jede Woche" in the text win. */
  const read = (at: number): Understood => {
    const found = understand(text, { now: at, zone, calendars: calendars.map((entry) => ({ id: entry.id, name: entry.name, writable: entry.writable })) })
    if (found.kind !== 'event') return found
    return withDefaults(found, calendars.find((entry) => entry.id === (found.calendarId ?? fallback)) ?? null)
  }
  // The device's clock, moving on by itself: "heute" and "morgen" follow it (EC-13).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const understood = useMemo(() => read(now), [text, zone, calendars, now, fallback])
  const chosenId = understood.kind === 'event' ? (understood.calendarId ?? fallback) : null
  const calendar = calendars.find((entry) => entry.id === chosenId) ?? null

  const change = (value: string) => {
    setText(value)
    setFailed(null)
    // Another text is another event: the same reference would give back the one saved before (EC-11).
    ref.current = newRef()
  }

  const fieldsOf = (event: Event): EventFields & { rule?: EventFields['rule'] } => ({
    title: event.title,
    all_day: event.allDay,
    start: event.allDay ? event.date : `${event.date}T${event.start}`,
    end: event.allDay ? event.endDate : `${event.endDate}T${event.end}`,
    time_zone: event.allDay ? '' : zone,
    ...(calendar ? { calendar_id: calendar.id } : {}),
    ...(event.rule ? { rule: event.rule } : {}),
    ...extrasOf(event, calendar),
  })

  const more = (event: Event) => {
    // Only what the text said goes over as the member's own; what came from the defaults stays a default there, so
    // another calendar chosen in the dialog brings its own (review of the additions, 2026-11-23).
    const typed = event.typed ?? { length: true, rule: true }
    const withEnd = event.allDay ? event.endDate !== event.date : typed.length
    open({
      kind: 'new',
      title: event.title,
      start: event.allDay ? event.date : `${event.date}T${event.start}`,
      ...(withEnd ? { end: event.allDay ? event.endDate : `${event.endDate}T${event.end}` } : {}),
      allDay: event.allDay,
      calendarId: calendar?.id,
      ...(typed.rule ? { rule: event.rule } : {}),
    })
    change('')
  }

  const enter = async () => {
    // Understood again with the clock of this moment: past midnight "morgen" is the new tomorrow (EC-13).
    const understood = read(Date.now())
    if (understood.kind === 'empty' || busy.current) return
    if (understood.kind === 'noDate') {
      open({ kind: 'new', title: understood.text })
      change('')
      return
    }
    if (understood.problem === 'long') return
    if (understood.problem) {
      more(understood)
      return
    }
    busy.current = true
    try {
      const made = await eventsApi.create(fieldsOf(understood), ref.current)
      showNotice({
        text: t('quick.saved', {
          title: made.title,
          when: understood.allDay ? quickDay(understood.date, language, String(new Date(now).getUTCFullYear())) : `${quickDay(understood.date, language, String(new Date(now).getUTCFullYear()))}, ${clockText(understood.start!, language, twelve)}`,
        }),
      })
      change('')
      announce()
    } catch (error) {
      // The server said no (a calendar now only to be read, a rule it refuses): its sentence, not "connection".
      setFailed(error instanceof ApiError && error.status > 0 ? problemText(error) : t('quick.failed'))
    } finally {
      busy.current = false
    }
  }

  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault()
        void enter()
      }}
    >
      <input
        type="text"
        className="nt-field"
        value={text}
        onChange={(event) => change(event.target.value)}
        onPaste={(event) => {
          // Pasted lines become one line (EC-6); a text field would drop the breaks and glue the words together.
          const pasted = event.clipboardData?.getData('text') ?? ''
          if (!/[\r\n]/.test(pasted)) return
          event.preventDefault()
          const field = event.currentTarget
          const begin = field.selectionStart ?? text.length
          const finish = field.selectionEnd ?? text.length
          change(text.slice(0, begin) + pasted.replace(/\s*[\r\n]+\s*/g, ' ') + text.slice(finish))
        }}
        placeholder={narrow ? t('quick.placeholderShort') : t('views.quickPlaceholder')}
        aria-label={t('views.quick')}
        aria-describedby="quick-preview"
        autoComplete="off"
        enterKeyHint="done"
        disabled={offline}
        data-testid="quick-entry"
      />
      <div id="quick-preview" aria-live="polite">
        {failed && (
          <p className="text-sm text-bad-500" role="alert" data-testid="quick-failed">
            {failed}
          </p>
        )}
        <Preview understood={understood} calendar={calendar} calendars={calendars} readOnlyAlone={understood.kind === 'event' && understood.calendarId === null ? suggested.readOnlyAlone : null} isDefault={!!calendar && understood.kind === 'event' && understood.calendarId === null && calendar.id === list?.default_id} onMore={more} />
      </div>
    </form>
  )
}

function Preview({ understood, calendar, calendars, readOnlyAlone, isDefault, onMore }: {
  understood: Understood
  calendar: CalendarEntry | null
  calendars: CalendarEntry[]
  /** The calendar shown alone may only be read: the event goes elsewhere, said as in the side panel (TIME-13 EC-18). */
  readOnlyAlone: string | null
  isDefault: boolean
  onMore: (event: Event) => void
}) {
  const { t, i18n } = useTranslation()
  const { twelve } = useDisplay()
  const language = i18n.language
  const thisYear = String(new Date().getFullYear())
  if (understood.kind === 'empty') return null
  if (understood.kind === 'noDate') {
    return (
      <p className="rounded-[10px] bg-[var(--color-warn-wash)] px-3 py-2 text-sm text-warn-500" data-testid="quick-preview" data-state="no-date">
        {t('quick.noDate', { text: understood.text })}
      </p>
    )
  }
  if (understood.problem) {
    const key = understood.problem === 'long' ? 'quick.tooLong' : understood.problem === 'end' ? 'quick.endBeforeStart' : understood.problem === 'noTime' ? 'quick.noTime' : 'quick.noTitle'
    return (
      <p className="rounded-[10px] bg-[var(--color-warn-wash)] px-3 py-2 text-sm text-warn-500" data-testid="quick-preview" data-state={understood.problem}>
        {t(key, { max: TITLE_MAX, count: understood.title.length })}
      </p>
    )
  }
  const time = (value: string) => clockText(value, language, twelve)
  const day = quickDay(understood.date, language, thisYear)
  const when = understood.rule
    ? `${ruleSentence(understood.rule, understood.date, understood.allDay ? { allDay: true, start: '', end: '' } : { allDay: false, start: understood.start!, end: understood.end! }, t, language, twelve)} · ${t('quick.from', { day })}`
    : understood.allDay
      ? `${day} · ${t('views.allDay')}`
      : `${day} · ${time(understood.start!)}–${time(understood.end!)}${understood.defaultLength ? ` ${t('quick.oneHour')}` : ''}`
  // Two writable calendars of the same name: the source says which one (EC-8).
  const twin = calendar && calendars.some((entry) => entry.id !== calendar.id && entry.writable && entry.name.trim().toLocaleLowerCase() === calendar.name.trim().toLocaleLowerCase())
  const name = calendar ? (twin ? `${calendar.name} (${t(`calendars.groups.${calendar.group}`)})` : calendar.name) : ''
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-[12px] border border-ink-700 bg-ink-850 px-3.5 py-2.5" data-testid="quick-preview" data-state="ok">
      <span className="h-9 w-1 shrink-0 self-stretch rounded-full" style={{ background: colorVar(calendar?.color ?? 'slate') }} aria-hidden data-color={calendar?.color} />
      <span className="flex min-w-0 flex-[1_1_220px] flex-col gap-0.5">
        <strong className="nt-wrap text-[14.5px] text-mist-100" data-testid="quick-title">
          {understood.title}
        </strong>
        <span className="nt-wrap text-[13px] text-mist-400" data-testid="quick-when">
          {when}
          {calendar ? ` · ${name}${isDefault ? t('quick.yourDefault') : ''}` : ''}
        </span>
        {readOnlyAlone && (
          <span className="nt-wrap text-[12.5px] text-warn-500" data-testid="quick-read-only">
            {t('collections.readOnlyAlone', { name: readOnlyAlone })}
          </span>
        )}
        {understood.tag && (
          <span className="nt-wrap text-[12.5px] text-warn-500" data-testid="quick-tag">
            {t(understood.tag.problem === 'readOnly' ? 'quick.tagReadOnly' : 'quick.tagUnknown', { tag: understood.tag.text, calendar: calendar?.name ?? '' })}
          </span>
        )}
      </span>
      <span className="text-xs text-mist-500">{t('quick.enterSaves')}</span>
      <button type="button" className="nt-btn nt-btn-quiet h-8 px-3 text-[13px]" onClick={() => onMore(understood)} data-testid="quick-more">
        {t('quick.more')}
      </button>
    </div>
  )
}

type QuickEvent = Extract<Understood, { kind: 'event' }>

/** The defaults of the calendar (TIME-4) where the text said nothing: the length of an event with a time, the repeat. */
function withDefaults(event: QuickEvent, calendar: CalendarEntry | null): Event {
  const chosen = defaultsOf(calendar)
  let next: Event = { ...event, typed: { length: !event.defaultLength, rule: !!event.rule } }
  if (!event.allDay && event.defaultLength && event.start && chosen.minutes) {
    const [year, month, day] = event.date.split('-').map(Number)
    const [hours, minutes] = event.start.split(':').map(Number)
    const end = new Date(Date.UTC(year, month - 1, day, hours, minutes + chosen.minutes)).toISOString()
    next = { ...next, endDate: end.slice(0, 10), end: end.slice(11, 16), defaultLength: chosen.minutes === 60 }
  }
  if (!event.rule && chosen.rule) next = { ...next, rule: ruleOf(chosen.rule, event.date) }
  return next
}

/** Busy and the reminders of the calendar's defaults (TIME-4); reminders only for the kind they were chosen for. */
function extrasOf(event: QuickEvent, calendar: CalendarEntry | null): Partial<EventFields> {
  const chosen = defaultsOf(calendar)
  return {
    ...(chosen.busy !== undefined ? { busy: chosen.busy } : {}),
    ...(remindersFor(chosen, event.allDay) !== undefined ? { reminders: remindersFor(chosen, event.allDay) } : {}),
  }
}
