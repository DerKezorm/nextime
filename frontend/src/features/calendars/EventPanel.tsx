/**
 * An event in the side panel on the right (TIME-5 AC-8): no dialog; the view behind stays visible, darkened. On top
 * color, calendar and source and "Close"; then the title, start and end, "All day", "Busy", the time zone, the
 * calendar, the rule (TIME-6), the places of reminders, location and attachments (later features), "Notes and links"
 * with "Join"; below "Delete", "Cancel", "Save". A calendar one may only read shows the event with the reason why
 * (AC-5); somebody's later save over one's own shows both versions (EC-2). Closing with unsaved changes asks first
 * (AC-14). An invitation shows as such, with its three answers (TIME-18 AC-4); deleting an open one asks whether to
 * decline instead (AC-8). Attendees show at every event that has them (AC-3).
 */
import { X } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { Avatar } from '../../components/Avatar'
import { Dialog } from '../../components/Dialog'
import { CheckLine } from '../../components/CheckLine'
import { knownZones, zonePlace } from '../../lib/display'
import { errorText, problemText } from '../../lib/errors'
import { showNotice } from '../../lib/notice'
import { trashApi } from '../trash/api'
import { ago } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { attachmentsApi, type AttachmentInfo, type Target } from '../attachments/api'
import { Attachments, RemoteAttachments } from '../attachments/Attachments'
import { busyFor, cancelAll } from '../attachments/uploads'
import { suggestion, useShown } from '../collections/store'
import type { Point } from '../places/api'
import { PlaceField, PlaceView } from '../places/Place'
import { remindersApi } from '../reminders/api'
import { ReminderChips } from '../reminders/ReminderChips'
import { withOccurrences } from '../reminders/leads'
import { Attendees, InvitationView } from '../sources/Invitations'
import { eventsApi, newRef, type Answer, type CalendarEntry, type EventDetail, type EventFields, type Rule, type Scope, type Source, type Summary } from './api'
import { joinLink, LinkedText } from './links'
import { RecurrenceDialog, SeriesQuestion } from './Recurrence'
import { SourceQuestion } from './SourceQuestion'
import { defaultRule, ruleSentence, rruleSentence } from './rule'
import { Swatch } from './Swatch'
import { announce, useCalendars, usePanel, type PanelTarget } from './store'
import { useOffline } from '../offline/connection'
import { useCompact } from '../views/state'
import { OfflinePanel } from '../offline/OfflinePanel'
import { applyDefaults, defaultsOf, remindersFor, ruleOf, type Touched } from './defaults'
import { clockText, dayName, instantOf, nextFullHour, validDate, validTime, wallOf } from './time'

const TITLE_MAX = 200
const NOTES_MAX = 10_000

type Form = {
  title: string
  calendarId: number | null
  allDay: boolean
  busy: boolean
  busyTouched: boolean
  startDate: string
  startTime: string
  endDate: string
  endTime: string
  /** The event's own zone; the zone shown when it is the account's. */
  zone: string
  notes: string
  rule: Rule | null
  /** The place and, for a chosen suggestion, where it lies (TIME-8). */
  location: string
  point: Point | null
  /** The member's own reminders (TIME-7); untouched, a new event follows the calendar's default. */
  reminders: number[]
  remindersTouched: boolean
  /** A new event: what the member set himself (or what came with where it was opened), so that the defaults of its
   * calendar (TIME-4) leave it alone, also when the calendar changes; the reminders the defaults brought go along. */
  allDayTouched: boolean
  /** The member chose the calendar himself (or it came with where the panel was opened): the suggestion leaves it. */
  calendarTouched: boolean
  timesTouched: boolean
  ruleTouched: boolean
  remindersFromDefaults: boolean
}

type Problems = Partial<Record<'title' | 'notes' | 'start' | 'end' | 'calendar' | 'rule' | 'zone' | 'location', string>>

const LOCATION_MAX = 2_000
const ALL_DAY_PRESETS = [360, -540]
const PRESETS = [5, 15, 30, 60, 1440]

/** What a new event in `calendar` gets as reminders while the member chose none (TIME-7 AC-3): the default of an own
 * calendar; a whole day "on the day at 09:00" under a day, else "the day before at 18:00". */
function defaultLeads(calendar: CalendarEntry | null, allDay: boolean): number[] {
  if (!calendar || calendar.right !== 'owner' || calendar.origin || !calendar.reminder) return []
  return [allDay ? (calendar.reminder >= 1440 ? 360 : -540) : calendar.reminder]
}

function formOf(detail: EventDetail, shown: string): Form {
  return {
    title: detail.title,
    calendarId: detail.calendar_id,
    allDay: detail.all_day,
    busy: detail.busy,
    busyTouched: true,
    startDate: detail.start.slice(0, 10),
    startTime: detail.all_day ? '09:00' : detail.start.slice(11, 16),
    endDate: detail.end.slice(0, 10),
    endTime: detail.all_day ? '10:00' : detail.end.slice(11, 16),
    zone: detail.time_zone || shown,
    notes: detail.notes,
    rule: detail.rule,
    location: detail.location,
    point: detail.place ?? null,
    reminders: detail.reminders?.minutes ?? [],
    remindersTouched: false,
    allDayTouched: true,
    calendarTouched: true,
    timesTouched: true,
    ruleTouched: true,
    remindersFromDefaults: false,
  }
}

function touchedOf(form: Form): Touched {
  return { allDay: form.allDayTouched, times: form.timesTouched, rule: form.ruleTouched, busy: form.busyTouched, reminders: form.remindersTouched }
}

function timesOf(form: Form): { all_day: boolean; start: string; end: string; time_zone: string } {
  return form.allDay
    ? { all_day: true, start: form.startDate, end: form.endDate, time_zone: '' }
    : { all_day: false, start: `${form.startDate}T${form.startTime}`, end: `${form.endDate}T${form.endTime}`, time_zone: form.zone }
}

export function EventPanelHost() {
  const { target } = usePanel()
  if (!target) return null
  return <PanelChoice key={target.kind === 'open' ? `${target.id}-${target.occurrence}` : 'new'} target={target} />
}

/** Without a connection when it opens, the panel reads the event from what is kept, to read only (TIME-28 AC-3); a
 * connection lost while it is open leaves it as it is, with all that was typed (EC-5). */
function PanelChoice({ target }: { target: PanelTarget }) {
  const offline = useOffline()
  const { close } = usePanel()
  const [readOnly] = useState(offline)
  const nothingNew = readOnly && target.kind === 'new'
  useEffect(() => {
    if (nothingNew) close()
  }, [nothingNew, close])
  if (!readOnly) return <EventPanel target={target} />
  return target.kind === 'open' ? <OfflinePanel target={target} /> : null
}

function EventPanel({ target }: { target: PanelTarget }) {
  const { t, i18n } = useTranslation()
  const { zone: shown, twelve, language } = useDisplay()
  const { close, setDirty } = usePanel()
  const { list } = useCalendars()
  const suggested = suggestion(list, useShown())
  const location = useLocation()
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [loadProblem, setLoadProblem] = useState<string | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [original, setOriginal] = useState<Form | null>(null)
  const [problems, setProblems] = useState<Problems>({})
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState<null | 'discard' | 'uploads' | { question: string; single: boolean; then: (scope: Scope) => void } | { sources: Source[]; deleting: boolean; then: (source: Source) => void }>(null)
  const [ruleOpen, setRuleOpen] = useState(false)
  const [answering, setAnswering] = useState(false)
  const [inviteNote, setInviteNote] = useState<string | null>(null)
  /** Deleting an open invitation asks first (TIME-18 AC-8); an answer to a series asks for which (EC-6). */
  const [inviteAsk, setInviteAsk] = useState<null | { kind: 'delete' } | { kind: 'series'; answer: Answer }>(null)
  const answerInFlight = useRef(false)
  const [zoneOpen, setZoneOpen] = useState(false)
  // Lost while the panel is open: what was typed stays, saving waits for the connection (TIME-28 AC-3, EC-5).
  const offline = useOffline()
  // Below 1024 px the panel at the edge, as before; wider a dialog in the middle (decided while testing, 2026-11-23).
  const compact = useCompact()
  const ref = useRef(newRef())
  const inFlight = useRef(false)
  const titleRef = useRef<HTMLInputElement>(null)
  const ids = { title: useId(), start: useId(), end: useId(), calendar: useId(), notes: useId(), zone: useId(), rule: useId() }

  // A new event: the next full hour, an hour long (AC-10), busy (AC-9), in the calendar the collection suggests
  // (TIME-13 AC-4, AC-13); from the grid at the time clicked or dragged (TIME-12 AC-3).
  useEffect(() => {
    if (target.kind !== 'new' || form || !list) return
    const begin = target.start ? { date: target.start.slice(0, 10), time: target.start.slice(11, 16) || '09:00' } : nextFullHour(shown)
    // An end without a time (a whole day from the all-day row) still gets one, for when "Ganztägig" is unticked.
    const end = target.end && target.end.length > 10 ? { date: target.end.slice(0, 10), time: target.end.slice(11, 16) } : wallOf(instantOf(begin.date, begin.time, shown) + 3_600_000, shown)
    const blank: Form = {
      title: target.title ?? '',
      calendarId: target.calendarId ?? suggested.id,
      allDay: !!target.allDay,
      busy: !target.allDay,
      busyTouched: false,
      startDate: begin.date,
      startTime: begin.time,
      endDate: target.allDay ? (target.end?.slice(0, 10) ?? begin.date) : end.date,
      endTime: end.time,
      zone: shown,
      notes: '',
      rule: target.rule ?? null,
      location: '',
      point: null,
      reminders: [],
      remindersTouched: false,
      // A time dragged or typed is the member's (TIME-4 defaults: what he gives wins); a click without one is not.
      allDayTouched: target.allDay !== undefined || !!target.end,
      calendarTouched: target.calendarId !== undefined,
      timesTouched: !!target.end,
      ruleTouched: !!target.rule,
      remindersFromDefaults: false,
    }
    const fresh = applyDefaults(blank, list.calendars.find((entry) => entry.id === blank.calendarId), touchedOf(blank))
    setForm(fresh)
    setOriginal(fresh)
  }, [target, form, list, shown, suggested.id])

  // The calendar the view suggests can come a moment after the panel opened (in the browser its first look may have
  // the usual calendar while "Nur diesen" is set, found while testing 2026-11-23). As long as the member has not
  // chosen one, a new event follows the suggestion, with that calendar's defaults for what is untouched (TIME-13
  // AC-13, TIME-4 defaults); the panel stays without changes of its own.
  useEffect(() => {
    if (target.kind !== 'new' || !form || !original || !list || form.calendarTouched) return
    if (suggested.id === null || suggested.id === form.calendarId) return
    const calendar = list.calendars.find((entry) => entry.id === suggested.id)
    const follow = (value: Form) => applyDefaults({ ...value, calendarId: suggested.id }, calendar, touchedOf(value))
    setForm(follow(form))
    setOriginal(follow(original))
  }, [target, form, original, list, suggested.id])

  useEffect(() => {
    if (target.kind !== 'open') return
    let live = true
    eventsApi
      .get(target.id, target.occurrence)
      .then((answer) => {
        if (!live) return
        setDetail(answer)
        const loaded = formOf(answer, shown)
        setForm(loaded)
        setOriginal(loaded)
      })
      .catch((error) => live && setLoadProblem(problemText(error)))
    return () => {
      live = false
    }
  }, [target, shown])

  const ready = form !== null
  const shellRef = useRef<HTMLElement>(null)
  // Focus goes into the dialog when it opens: the title, or for an event only to be read or an invitation the first
  // control in it; on closing it goes back where it was (review of the additions, 2026-11-23).
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    return () => {
      if (before && before.isConnected && typeof before.focus === 'function') before.focus()
    }
  }, [])
  const focused = useRef(false)
  useEffect(() => {
    if (focused.current) return
    if (titleRef.current) {
      focused.current = true
      return titleRef.current.focus()
    }
    const shell = shellRef.current
    if (!shell || (!ready && !detail)) return
    focused.current = true
    if (shell.contains(document.activeElement)) return
    shell.querySelector<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href]')?.focus()
  }, [ready, detail])

  /** Where files of this panel go (TIME-9): the event, or for a new one the reference it will be made with (AC-10). */
  const uploadTarget: Target | null = target.kind === 'new' ? { ref: ref.current } : detail ? { event: detail.id } : null
  const [files, setFiles] = useState<AttachmentInfo[]>([])
  const dirty = useMemo(
    () => (!!form && !!original && JSON.stringify(form) !== JSON.stringify(original)) || (target.kind === 'new' && files.length > 0),
    [form, original, target.kind, files.length],
  )
  useEffect(() => setDirty(dirty), [dirty, setDirty])

  /** Closing without saving: what this panel uploads stops, the files of a new event go (TIME-9 AC-10). */
  const discard = () => {
    if (uploadTarget) cancelAll(uploadTarget)
    if (target.kind === 'new') for (const file of files) void attachmentsApi.remove(file.id).catch(() => undefined)
    close()
  }

  // Another view while the panel holds changes: ask (AC-14).
  const firstPath = useRef(location.pathname)
  useEffect(() => {
    if (location.pathname === firstPath.current) return
    firstPath.current = location.pathname
    if (dirty) setAsking('discard')
    else close()
  }, [location.pathname, dirty, close])

  const tryClose = () => {
    // An upload still running: closing without saving asks first (TIME-9 AC-10).
    if (uploadTarget && busyFor(uploadTarget)) return setAsking('uploads')
    if (dirty) setAsking('discard')
    else close()
  }

  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      // Another dialog over it closes first (its own Escape); the event's own dialog is not one of those.
      if (event.key === 'Escape' && !asking && !ruleOpen && !document.querySelector('[role="dialog"]:not([data-event-dialog])')) tryClose()
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  })

  const calendars = list?.calendars ?? []
  const calendar = calendars.find((entry) => entry.id === (form?.calendarId ?? detail?.calendar_id)) ?? null
  const places = list?.places ?? true
  /** A new event shows its calendar's default until the member chooses (TIME-7 AC-3). */
  const shownReminders = !form ? [] : form.remindersTouched || form.remindersFromDefaults || target.kind === 'open' ? form.reminders : defaultLeads(calendar, form.allDay)
  const leadNote = !form || shownReminders.length === 0 ? undefined : detail?.shared ? t('reminders.ownOnly') : !form.remindersTouched && (target.kind === 'new' || !detail?.reminders?.own) ? t('reminders.fromDefault') : undefined
  const reminderNote = withOccurrences(leadNote, detail?.reminders, t)
  /** Attachments only at events of nextime's own calendars (TIME-9 AC-5). */
  const ownCalendar = target.kind === 'new' ? !calendar?.origin : !detail?.source
  const movingTo = form && detail && form.calendarId !== detail.calendar_id ? calendar : null
  const moveLoses = movingTo?.origin ? files.length : 0
  const readOnly = target.kind === 'open' && detail !== null && !detail.writable
  /** Every calendar the event stands in, when it is shown once from several (TIME-14 AC-3). */
  const sources = target.kind === 'open' && target.sources && target.sources.length > 1 ? target.sources : null
  const elsewhere = sources ? sources.filter((source) => source.calendar_id !== detail?.calendar_id).map((source) => calendars.find((entry) => entry.id === source.calendar_id)?.name ?? '') : []

  const set = (change: Partial<Form>) => setForm((value) => (value ? { ...value, ...change } : value))

  const switchAllDay = (allDay: boolean) => {
    if (!form) return
    // "Busy" follows its default as long as nobody touched it (EC-12); the reminders of the defaults follow the kind
    // (a whole day and an event with a time have their own, decided after the review, 2026-11-23).
    const reminders = form.remindersTouched ? undefined : remindersFor(defaultsOf(calendar), allDay)
    set({
      allDay,
      allDayTouched: true,
      // A "busy" of the calendar's defaults holds for both kinds (review of the additions, 2026-11-23).
      busy: form.busyTouched ? form.busy : (defaultsOf(calendar).busy ?? !allDay),
      endDate: allDay && form.endDate < form.startDate ? form.startDate : form.endDate,
      ...(form.remindersTouched ? {} : { reminders: reminders ?? [], remindersFromDefaults: reminders !== undefined }),
    })
  }

  /** Another calendar for an unsaved new event: its defaults for all the member has not set himself (TIME-4). */
  const chooseCalendar = (calendarId: number) => {
    if (!form) return
    const next = { ...form, calendarId, calendarTouched: true }
    setForm(target.kind === 'new' ? applyDefaults(next, calendars.find((entry) => entry.id === calendarId), touchedOf(form)) : next)
  }

  const check = (value: Form): Problems => {
    const found: Problems = {}
    const title = value.title.trim()
    if (!title) found.title = t('panel.titleMissing')
    else if (title.length > TITLE_MAX) found.title = t('panel.tooLong', { max: TITLE_MAX.toLocaleString(language), now: title.length.toLocaleString(language) })
    if (value.notes.length > NOTES_MAX) found.notes = t('panel.tooLong', { max: NOTES_MAX.toLocaleString(language), now: value.notes.length.toLocaleString(language) })
    if (!validDate(value.startDate) || (!value.allDay && !validTime(value.startTime))) found.start = t('panel.invalidDate')
    if (!validDate(value.endDate) || (!value.allDay && !validTime(value.endTime))) found.end = t('panel.invalidDate')
    if (!found.start && !found.end) {
      const before = value.allDay ? value.endDate < value.startDate : instantOf(value.endDate, value.endTime, value.zone) < instantOf(value.startDate, value.startTime, value.zone)
      if (before) found.end = t('panel.endBeforeStart', { start: value.allDay ? dayName(value.startDate, language) : clockText(value.startTime, language, twelve) })
    }
    if (!value.calendarId) found.calendar = errorText('no_writable_calendar')
    if (value.location.trim().length > LOCATION_MAX) found.location = t('panel.tooLong', { max: LOCATION_MAX.toLocaleString(language), now: value.location.trim().length.toLocaleString(language) })
    return found
  }

  const fail = (error: unknown) => {
    if (error instanceof ApiError) {
      const field = String(error.values.field ?? '')
      const text = errorText(error.code, error.values)
      const where: Record<string, keyof Problems> = { title: 'title', notes: 'notes', start: 'start', end: 'end', calendar_id: 'calendar', time_zone: 'zone', location: 'location' }
      if (where[field]) return setProblems({ [where[field]]: text })
      if (field.startsWith('rule')) return setProblems({ rule: text })
      if (error.code === 'event_deleted') return setMessage(t('panel.deletedBy', { name: String(error.values.by || '') }))
      if (error.code === 'calendar_read_only' && error.values.owner) return setMessage(t('panel.rightTaken', { name: String(error.values.owner) }))
      return setMessage(text)
    }
    setMessage(t('panel.offline'))
  }

  const changes = (value: Form, before: Form): EventFields => {
    const fields: EventFields = {}
    if (value.title.trim() !== before.title.trim()) fields.title = value.title.trim()
    if (value.notes !== before.notes) fields.notes = value.notes
    if (value.busy !== before.busy) fields.busy = value.busy
    if (value.calendarId !== before.calendarId && value.calendarId) fields.calendar_id = value.calendarId
    const times = timesOf(value)
    if (JSON.stringify(times) !== JSON.stringify(timesOf(before))) Object.assign(fields, times)
    if (JSON.stringify(value.rule) !== JSON.stringify(before.rule)) fields.rule = value.rule
    if (value.location.trim() !== before.location.trim() || JSON.stringify(value.point) !== JSON.stringify(before.point)) {
      fields.location = value.location.trim()
      fields.geo = value.point
    }
    return fields
  }

  /** The member's own reminders changed (TIME-7): they go on their own, they change nothing of the event for others. */
  const remindersChanged = (value: Form, before: Form) => value.remindersTouched && JSON.stringify(value.reminders) !== JSON.stringify(before.reminders)

  const save = async () => {
    if (!form || !original || inFlight.current) return
    const found = check(form)
    setProblems(found)
    setMessage(null)
    if (Object.keys(found).length) return
    if (target.kind === 'new') {
      inFlight.current = true
      setBusy(true)
      try {
        await eventsApi.create(
          {
            calendar_id: form.calendarId ?? undefined,
            title: form.title.trim(),
            notes: form.notes,
            busy: form.busy,
            rule: form.rule,
            location: form.location.trim(),
            geo: form.location.trim() ? form.point : null,
            ...(form.remindersTouched || form.remindersFromDefaults ? { reminders: form.reminders } : {}),
            ...timesOf(form),
          },
          ref.current,
        )
        announce()
        close()
      } catch (error) {
        fail(error)
      } finally {
        inFlight.current = false
        setBusy(false)
      }
      return
    }
    if (!detail) return
    const fields = changes(form, original)
    if (remindersChanged(form, original)) {
      try {
        await remindersApi.forEvent(detail.id, form.reminders)
      } catch (error) {
        return fail(error)
      }
      if (Object.keys(fields).length === 0) {
        announce()
        return close()
      }
    }
    if (Object.keys(fields).length === 0) return close()
    // Shown once from several calendars: in which does it change (TIME-14 EC-2)?
    if (sources) {
      setAsking({ sources, deleting: false, then: (source: Source) => void inSource(source, (other) => sendTo(other, fields)) })
      return
    }
    sendTo(detail, fields)
  }

  /** The event as it stands in `source` (its version and occurrence there), then `then`. */
  const inSource = async (source: Source, then: (other: EventDetail) => void) => {
    setAsking(null)
    if (detail && source.id === detail.id && source.occurrence === detail.occurrence) return then(detail)
    try {
      then(await eventsApi.get(source.id, source.occurrence))
    } catch (error) {
      fail(error)
    }
  }

  const sendTo = (detail: EventDetail, fields: EventFields) => {
    if (!form || !original) return
    const send = async (scope: Scope) => {
      setAsking(null)
      setBusy(true)
      try {
        await eventsApi.change(detail.id, { ...fields, scope, occurrence: detail.occurrence, etag: detail.etag })
        announce()
        close()
      } catch (error) {
        fail(error)
      } finally {
        setBusy(false)
      }
    }
    if (!detail.recurring || !detail.occurrence) return void send('all')
    const moved = fields.start !== undefined
    const time = form.allDay ? '' : clockText(form.startTime, language, twelve)
    const question = moved
      ? form.startDate === original.startDate
        ? t('series.move', { title: original.title, when: time })
        : t('series.move', { title: original.title, when: `${dayName(form.startDate, language)} ${time}`.trim() })
      : t('series.change', { title: original.title })
    setAsking({ question, single: fields.rule === undefined && fields.calendar_id === undefined, then: (scope: Scope) => void send(scope) })
  }

  const remove = () => {
    if (!detail || !original) return
    if (sources) {
      setAsking({ sources, deleting: true, then: (source: Source) => void inSource(source, (other) => removeIn(other)) })
      return
    }
    removeIn(detail)
  }

  const removeIn = (detail: EventDetail) => {
    if (!original) return
    const go = async (scope: Scope) => {
      setAsking(null)
      setBusy(true)
      try {
        const answer = await eventsApi.remove(detail.id, scope, detail.occurrence)
        announce()
        close()
        // In the trash now (TIME-23): the server names the entry it became, a whole event or series or an occurrence
        // or the following ones, and "Undo" brings it back from there, as nexnotes does.
        const entry = answer?.trash
        const title = original.title
        showNotice({
          text: t('panel.deleted', { title }),
          undo: entry
            ? async () => {
                await trashApi.restore(entry)
                announce()
                return t('trash.restored', { title })
              }
            : undefined,
        })
      } catch (error) {
        fail(error)
        setBusy(false)
      }
    }
    if (detail.recurring && detail.occurrence) setAsking({ question: t('series.delete', { title: original.title }), single: true, then: (scope: Scope) => void go(scope) })
    else void go('all')
  }

  const resolve = async (restore: boolean) => {
    if (!detail) return
    try {
      const answer = await eventsApi.conflict(detail.id, restore)
      setDetail(answer)
      const loaded = formOf(answer, shown)
      setForm(loaded)
      setOriginal(loaded)
      announce()
    } catch (error) {
      fail(error)
    }
  }

  /**
   * The member's answer (TIME-18 AC-2, AC-5): sent once however often it is clicked (EC-5); a series asks first
   * whether it is for this event or all (EC-6); an invitation that changed meanwhile shows its new state (EC-1);
   * after "Absagen" it leaves the views and a short message says it stays in the search (AC-7).
   */
  const answer = async (value: Answer, scope?: 'this' | 'all') => {
    if (!detail || answerInFlight.current) return
    if (!scope && detail.recurring && detail.occurrence) return setInviteAsk({ kind: 'series', answer: value })
    setInviteAsk(null)
    answerInFlight.current = true
    setAnswering(true)
    setInviteNote(null)
    setMessage(null)
    try {
      const next = await eventsApi.answer(detail.id, value, scope ?? 'all', detail.occurrence, detail.etag)
      announce()
      if (value === 'declined') {
        showNotice({ text: t('invitations.declined', { title: detail.title }) })
        close()
        return
      }
      setDetail(next)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'invitation_changed') {
        try {
          setDetail(await eventsApi.get(detail.id, detail.occurrence))
          setInviteNote(t('invitations.changed'))
          announce()
        } catch (again) {
          fail(again)
        }
      } else fail(error)
    } finally {
      answerInFlight.current = false
      setAnswering(false)
    }
  }

  const invitation = target.kind === 'open' && detail?.invitation ? detail.invitation : null

  const heading = target.kind === 'new' ? t('panel.newTitle') : t('panel.title')
  const head = (
    <div className="flex items-center gap-2.5 border-b border-ink-700 px-5 py-3">
      {calendar && <Swatch color={calendar.color} icon={calendar.icon} />}
      <span className="min-w-0 flex-1 truncate text-sm text-mist-200" title={headLine(calendar, detail, t, language, elsewhere)} data-testid="panel-head">
        {headLine(calendar, detail, t, language, elsewhere)}
      </span>
      <button type="button" aria-label={t('common.close')} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100" onClick={tryClose} data-testid="panel-close">
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  )

  /** From 1024 px the event opens as a dialog in the middle, built like nexnotes' (decided while testing, 2026-11-23);
   * narrower it stays the panel at the edge. Tab stays inside the dialog. */
  const trap = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (compact || event.key !== 'Tab') return
    const inside = [...event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])')]
      .filter((element) => element.tabIndex >= 0)
    if (inside.length === 0) return
    const first = inside[0]
    const last = inside[inside.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div className={'fixed inset-0 z-40 flex ' + (compact ? 'justify-end' : 'items-center justify-center p-4')} data-testid="event-panel" data-shape={compact ? 'panel' : 'dialog'}>
      <button type="button" tabIndex={-1} aria-label={t('panel.closeBehind')} className={compact ? 'flex-1 bg-scrim' : 'absolute inset-0 bg-scrim'} onClick={tryClose} data-testid="panel-behind" />
      <aside
        aria-label={heading}
        role={compact ? undefined : 'dialog'}
        aria-modal={compact ? undefined : true}
        data-event-dialog={compact ? undefined : ''}
        onKeyDown={trap}
        ref={shellRef}
        className={
          compact
            ? 'flex h-full w-full max-w-[460px] flex-col border-l border-ink-700 bg-ink-850 shadow-2xl'
            : 'relative flex max-h-[calc(100dvh-2rem)] w-full max-w-[620px] min-w-0 flex-col rounded-[16px] border border-ink-700 bg-ink-850 shadow-2xl shadow-black/50'
        }
        data-testid="event-shell"
      >
        {head}
        <div className="nt-scroll min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {loadProblem && <Note tone="bad">{loadProblem}</Note>}
          {offline && form && (
            <Note tone="warn">
              <span data-testid="panel-offline">{t('offline.panel')}</span>
            </Note>
          )}
          {detail?.conflict && <Conflict detail={detail} onRestore={() => void resolve(true)} onLeave={() => void resolve(false)} />}
          {!form && !loadProblem && <div className="h-24 animate-pulse rounded-[12px] bg-ink-800" aria-hidden />}
          {form && invitation && detail && (
            <>
              <InvitationView detail={detail} calendarName={calendar?.name ?? ''} busy={answering} note={inviteNote} onAnswer={(value) => void answer(value)} />
              {message && (
                <Note tone="bad">
                  <span data-testid="panel-message">{message}</span>
                </Note>
              )}
            </>
          )}
          {form && !invitation && readOnly && detail && <ReadOnly detail={detail} calendar={calendar} places={places} />}
          {form && !invitation && !readOnly && (
            <>
              {target.kind === 'new' && suggested.readOnlyAlone && !form.calendarTouched && (
                <Note tone="warn">{t('collections.readOnlyAlone', { name: suggested.readOnlyAlone })}</Note>
              )}
              {target.kind === 'new' && list?.default_moved && list.default_id === form.calendarId && (
                <Note tone="warn">{t(`panel.defaultMoved.${list.default_moved}`, { was: list.default_was ?? '', now: calendar?.name ?? '' })}</Note>
              )}
              <div>
                <input
                  ref={titleRef}
                  id={ids.title}
                  value={form.title}
                  onChange={(event) => set({ title: event.target.value })}
                  placeholder={t('panel.titlePlaceholder')}
                  aria-label={t('panel.titleLabel')}
                  aria-invalid={problems.title ? true : undefined}
                  className={'nt-field h-11 text-base font-semibold ' + (problems.title ? 'border-bad-500' : '')}
                  data-testid="field-title"
                />
                <FieldProblem text={problems.title} />
              </div>
              {detail && <People detail={detail} />}
              {detail?.waiting && (
                <span className="nt-waiting self-start" data-testid="waiting">
                  {t('views.waiting')}
                </span>
              )}
              {/* Start and end one below the other, each with date and time side by side: a date beside a time in half the
                  panel cut the year off ("09.11.20"). */}
              <div className="space-y-3" data-testid="times">
                <TimeField id={ids.start} label={t('panel.start')} date={form.startDate} time={form.startTime} allDay={form.allDay} problem={problems.start}
                  onDate={(value) =>
                    set({
                      startDate: value,
                      endDate: form.endDate < value || form.endDate === form.startDate ? value : form.endDate,
                      // A weekly repeat from the defaults follows the new day ("Jeden Montag", review 2026-11-23).
                      ...(!form.ruleTouched && defaultsOf(calendar).rule ? { rule: ruleOf(defaultsOf(calendar).rule, value) } : {}),
                    })
                  }
                  onTime={(value) => set({ startTime: value, timesTouched: true })} testId="start" />
                <TimeField id={ids.end} label={t('panel.end')} date={form.endDate} time={form.endTime} allDay={form.allDay} problem={problems.end}
                  onDate={(value) => set({ endDate: value, timesTouched: true })} onTime={(value) => set({ endTime: value, timesTouched: true })} testId="end" />
              </div>
              <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
                <SwitchRow label={t('panel.allDay')} checked={form.allDay} onChange={switchAllDay} testId="switch-all-day" />
                <SwitchRow label={t('panel.busy')} checked={form.busy} onChange={(value) => set({ busy: value, busyTouched: true })} testId="switch-busy" />
                {!form.allDay && (
                  <span className="flex items-center gap-2">
                    <span className="text-mist-500" data-testid="zone-name">
                      {form.zone === shown ? t('panel.ownZone', { place: zonePlace(shown) }) : zonePlace(form.zone)}
                    </span>
                    <button type="button" className="nt-btn nt-btn-quiet text-sm" onClick={() => setZoneOpen(!zoneOpen)} data-testid="other-zone">
                      {t('panel.otherZone')}
                    </button>
                  </span>
                )}
              </div>
              {!form.allDay && zoneOpen && (
                <label className="block text-sm">
                  <span className="font-medium text-mist-100">{t('panel.zone')}</span>
                  <select value={form.zone} onChange={(event) => set({ zone: event.target.value })} className="nt-field mt-1" data-testid="field-zone">
                    {[shown, ...knownZones().filter((zone) => zone !== shown)].map((zone) => (
                      <option key={zone} value={zone}>
                        {zone === shown ? t('panel.ownZone', { place: zonePlace(shown) }) : zonePlace(zone)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {!form.allDay && form.zone !== shown && validDate(form.startDate) && validTime(form.startTime) && (
                <p className="text-xs text-mist-500" data-testid="zone-hint">
                  {zoneHint(form, shown, language, twelve, t)}
                </p>
              )}
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block text-sm">
                  <span className="font-medium text-mist-100">{t('panel.calendar')}</span>
                  <span className="mt-1 flex items-center gap-2">
                    {calendar && <Swatch color={calendar.color} icon={calendar.icon} />}
                    <select id={ids.calendar} value={form.calendarId ?? ''} onChange={(event) => chooseCalendar(Number(event.target.value))} className={'nt-field ' + (problems.calendar ? 'border-bad-500' : '')} data-testid="field-calendar">
                      {calendars.filter((entry) => entry.writable).map((entry) => (
                        <option key={entry.id} value={entry.id}>
                          {entry.name}
                        </option>
                      ))}
                    </select>
                  </span>
                  <FieldProblem text={problems.calendar} />
                </label>
                <div className="text-sm">
                  <span id={ids.rule} className="font-medium text-mist-100">
                    {t('panel.repeat')}
                  </span>
                  <button type="button" aria-labelledby={ids.rule} className="nt-field mt-1 truncate text-left" onClick={() => setRuleOpen(true)} data-testid="field-rule">
                    {form.rule ? ruleSentence(form.rule, ruleFirst(form, detail), null, t, i18n.language) : detail?.rrule ? rruleSentence(detail.rrule, ruleFirst(form, detail), t, i18n.language) : t('rule.never')}
                  </button>
                  <FieldProblem text={problems.rule} />
                </div>
              </div>
              <ReminderChips
                minutes={shownReminders}
                presets={form.allDay ? ALL_DAY_PRESETS : PRESETS}
                allDay={form.allDay}
                onChange={(reminders) => set({ reminders, remindersTouched: true, remindersFromDefaults: false })}
                note={reminderNote}
              />
              <div className="space-y-2">
                <PlaceField value={form.location} onChange={(location, point) => set({ location, point })} enabled={places} label={t('panel.location')} problem={problems.location} />
                <PlaceView location={form.location} point={form.point} enabled={places} lookup={!!original && form.location === original.location} withText={false} />
              </div>
              <div className="text-sm">
                <label htmlFor={ids.notes} className="font-medium text-mist-100">
                  {t('panel.notes')}
                </label>
                <textarea id={ids.notes} value={form.notes} rows={4} onChange={(event) => set({ notes: event.target.value })} className={'nt-field mt-1 h-auto py-2 ' + (problems.notes ? 'border-bad-500' : '')} data-testid="field-notes" />
                <FieldProblem text={problems.notes} />
                <NotesLinks notes={form.notes} />
              </div>
              {moveLoses > 0 && movingTo?.origin && (
                <Note tone="warn">
                  <span data-testid="attachments-move-loses">{t('attachments.moveLoses', { count: moveLoses, source: t(`attachments.sourceNames.${movingTo.origin.kind}`) })}</span>
                </Note>
              )}
              {ownCalendar && uploadTarget ? (
                <Attachments
                  target={uploadTarget}
                  items={detail?.attachments ?? emptyFiles}
                  allowed={target.kind === 'new' ? true : !!detail?.attachments_allowed}
                  maxMb={detail?.attachment_max_mb ?? list?.attachment_max_mb ?? 25}
                  onItems={setFiles}
                />
              ) : detail ? (
                <RemoteAttachments eventId={detail.id} items={detail.remote_attachments ?? []} source={detail.source ?? ''} />
              ) : (
                <p className="text-xs text-mist-500" data-testid="attachments-own-only">
                  {t('attachments.ownOnly')}
                </p>
              )}
              {detail && <Attendees detail={detail} />}
              {message && (
                <Note tone="bad">
                  <span data-testid="panel-message">{message}</span>
                </Note>
              )}
            </>
          )}
        </div>
        <div className={'flex items-center gap-2 px-5 py-3 ' + (compact ? 'border-t border-ink-700' : 'pb-4')} data-testid="event-footer">
          {invitation && form ? (
            <>
              <button
                type="button"
                className="nt-btn nt-btn-quiet mr-auto text-bad-500"
                disabled={busy || answering}
                onClick={() => (invitation.state === 'open' && invitation.can_answer && !invitation.cancelled ? setInviteAsk({ kind: 'delete' }) : remove())}
                data-testid="panel-delete"
              >
                {t('panel.delete')}
              </button>
              <button type="button" className="nt-btn nt-btn-accent" onClick={close} data-testid="panel-close-invitation">
                {t('common.close')}
              </button>
            </>
          ) : readOnly || !form ? (
            <button type="button" className="nt-btn nt-btn-accent ml-auto" onClick={close}>
              {t('common.close')}
            </button>
          ) : (
            <>
              {target.kind === 'open' && (
                <button type="button" className="nt-btn nt-btn-quiet mr-auto text-bad-500" disabled={busy || offline} onClick={remove} data-testid="panel-delete">
                  {t('panel.delete')}
                </button>
              )}
              <button type="button" className="nt-btn nt-btn-ghost ml-auto" onClick={close} data-testid="panel-cancel">
                {t('common.cancel')}
              </button>
              <button type="button" className="nt-btn nt-btn-accent" disabled={busy || offline} onClick={() => void save()} data-testid="panel-save">
                {t('panel.save')}
              </button>
            </>
          )}
        </div>
      </aside>
      {ruleOpen && form && (
        <RecurrenceDialog
          title={form.title}
          first={ruleFirst(form, detail)}
          times={{ allDay: form.allDay, start: form.startTime, end: form.endTime }}
          rule={form.rule ?? defaultRule(ruleFirst(form, detail))}
          twelve={twelve}
          onApply={(rule) => {
            set({ rule, ruleTouched: true })
            setRuleOpen(false)
          }}
          onClose={() => setRuleOpen(false)}
        />
      )}
      {inviteAsk?.kind === 'delete' && invitation && (
        <Dialog title={t('invitations.deleteTitle')} onClose={() => setInviteAsk(null)}>
          <div className="space-y-4" data-testid="decline-instead">
            <p className="text-sm text-mist-200">{t('invitations.deleteText', { name: invitation.by })}</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setInviteAsk(null)}>
                {t('common.cancel')}
              </button>
              <button type="button" className="nt-btn nt-btn-ghost" onClick={() => (setInviteAsk(null), remove())} data-testid="delete-without-answer">
                {t('invitations.deleteWithout')}
              </button>
              <button type="button" className="nt-btn nt-btn-accent" onClick={() => (setInviteAsk(null), void answer('declined'))} data-testid="decline-instead-go">
                {t('invitations.decline')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
      {inviteAsk?.kind === 'series' && detail && (
        <Dialog title={t('invitations.seriesTitle')} onClose={() => setInviteAsk(null)}>
          <div className="flex flex-col gap-2" data-testid="answer-series">
            <p className="text-sm text-mist-200">{t('series.text', { day: dayName(detail.start.slice(0, 10), language) })}</p>
            <button type="button" className="nt-btn nt-btn-ghost justify-start" onClick={() => void answer(inviteAsk.answer, 'this')} data-testid="answer-this">
              {t('series.this')}
            </button>
            <button type="button" className="nt-btn nt-btn-ghost justify-start" onClick={() => void answer(inviteAsk.answer, 'all')} data-testid="answer-all">
              {t('series.all')}
            </button>
            <button type="button" className="nt-btn nt-btn-quiet self-end" onClick={() => setInviteAsk(null)}>
              {t('common.cancel')}
            </button>
          </div>
        </Dialog>
      )}
      {asking === 'uploads' && (
        <Dialog title={t('attachments.running')} onClose={() => setAsking(null)}>
          <div className="space-y-4" data-testid="uploads-question">
            <p className="text-sm text-mist-200">{t('attachments.runningText')}</p>
            <div className="flex justify-end gap-2">
              <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setAsking(null)}>
                {t('attachments.keepOpen')}
              </button>
              <button type="button" className="nt-btn nt-btn-danger" onClick={discard} data-testid="uploads-close">
                {t('attachments.closeAnyway')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
      {asking === 'discard' && (
        <Dialog title={t('panel.discardTitle')} onClose={() => setAsking(null)}>
          <div className="flex justify-end gap-2" data-testid="discard-question">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setAsking(null)}>
              {t('panel.discardNo')}
            </button>
            <button type="button" className="nt-btn nt-btn-danger" onClick={discard}>
              {t('panel.discardYes')}
            </button>
          </div>
        </Dialog>
      )}
      {asking && typeof asking === 'object' && 'sources' in asking && original && (
        <SourceQuestion title={original.title} sources={asking.sources} calendars={calendars} deleting={asking.deleting} onChoose={asking.then} onClose={() => setAsking(null)} />
      )}
      {asking && typeof asking === 'object' && 'question' in asking && detail && original && (
        <SeriesQuestion question={asking.question} day={dayName(detail.start.slice(0, 10), language)} single={asking.single} onChoose={asking.then} onClose={() => setAsking(null)} />
      )}
    </div>
  )
}

/** The rule counts from the start of the series; a new one from the start of the event. */
function ruleFirst(form: Form, detail: EventDetail | null): string {
  return detail?.recurring ? detail.series_start.start.slice(0, 10) : form.startDate
}

function headLine(calendar: CalendarEntry | null, detail: EventDetail | null, t: (key: string, values?: Record<string, unknown>) => string, language: string, elsewhere: string[] = []): string {
  if (!calendar) return ''
  const parts = [calendar.owner ? `${calendar.name} · ${calendar.owner}` : calendar.name]
  if (elsewhere.length) parts.push(t('panel.alsoIn', { names: elsewhere.join(', ') }))
  if (detail?.shared && detail.updated_by && detail.updated_by.id !== detail.created_by?.id && !detail.busy_only) {
    parts.push(t('panel.changedBy', { name: detail.updated_by.name, when: ago(Date.parse(detail.updated_at), language) }))
  }
  // Changed last by a program through a key (TIME-26 AC-10): the key named, for any calendar.
  if (detail?.updated_via && !detail.busy_only) parts.push(t('panel.changedVia', { key: detail.updated_via }))
  return parts.join(' · ')
}

function zoneHint(form: Form, shown: string, language: string, twelve: boolean, t: (key: string, values?: Record<string, unknown>) => string): string {
  const instant = instantOf(form.startDate, form.startTime, form.zone)
  const here = wallOf(instant, shown)
  return t('panel.zoneHint', { time: clockText(form.startTime, language, twelve), place: zonePlace(form.zone), day: dayName(here.date, language), local: clockText(here.time, language, twelve) })
}

function TimeField({ id, label, date, time, allDay, problem, onDate, onTime, testId }: { id: string; label: string; date: string; time: string; allDay: boolean; problem?: string; onDate: (value: string) => void; onTime: (value: string) => void; testId: string }) {
  const { t } = useTranslation()
  return (
    <div className="text-sm">
      <label htmlFor={id} className="font-medium text-mist-100">
        {label}
      </label>
      <div className="mt-1 flex flex-wrap gap-1.5" data-testid={`time-row-${testId}`}>
        <input id={id} type="date" value={date} onChange={(event) => onDate(event.target.value)} aria-invalid={problem ? true : undefined} className={'nt-field nt-date flex-1 ' + (problem ? 'border-bad-500' : '')} data-testid={`field-${testId}-date`} />
        {!allDay && (
          <input type="time" step={900} value={time} onChange={(event) => onTime(event.target.value)} aria-label={t('panel.timeOf', { label })} className={'nt-field nt-time ' + (problem ? 'border-bad-500' : '')} data-testid={`field-${testId}-time`} />
        )}
      </div>
      <FieldProblem text={problem} />
    </div>
  )
}

function SwitchRow({ label, checked, onChange, testId }: { label: string; checked: boolean; onChange: (value: boolean) => void; testId: string }) {
  return <CheckLine label={label} checked={checked} onChange={onChange} testId={testId} />
}

function FieldProblem({ text }: { text?: string }) {
  if (!text) return null
  return (
    <span role="alert" className="mt-1 block text-xs text-bad-500">
      {text}
    </span>
  )
}

function Note({ tone, children }: { tone: 'bad' | 'warn' | 'plain'; children: ReactNode }) {
  const look = tone === 'bad' ? 'border-bad-500/30 bg-bad-500/10 text-bad-500' : tone === 'warn' ? 'border-warn-500/30 bg-warn-wash text-warn-500' : 'border-ink-700 bg-[var(--color-accent-wash)] text-mist-200'
  return (
    <div role="status" className={'rounded-[9px] border px-3 py-2 text-sm ' + look}>
      {children}
    </div>
  )
}

const emptyFiles: AttachmentInfo[] = []

function NotesLinks({ notes }: { notes: string }) {
  const { t } = useTranslation()
  const join = joinLink(notes)
  const hasLink = /https?:\/\/|mailto:|tel:/i.test(notes)
  if (!hasLink) return null
  return (
    <div className="mt-2 space-y-2">
      <p className="text-xs break-words whitespace-pre-wrap text-mist-400" data-testid="notes-links">
        <LinkedText text={notes} />
      </p>
      {join && (
        <a href={join} target="_blank" rel="noopener noreferrer" className="nt-btn h-9 bg-[var(--color-accent-wash)] text-accent-400" data-testid="join">
          {t('panel.join')}
        </a>
      )}
    </div>
  )
}

function People({ detail }: { detail: EventDetail }) {
  const { t } = useTranslation()
  if (!detail.shared || !detail.created_by || detail.busy_only) return null
  return (
    <div className="flex items-center gap-2 text-sm text-mist-200" data-testid="entered-by">
      <Avatar accountId={detail.created_by.id} name={detail.created_by.name} stamp={detail.created_by.avatar} color={detail.created_by.color} size={22} />
      {t('panel.enteredBy', { name: detail.created_by.name })}
    </div>
  )
}

function ReadOnly({ detail, calendar, places }: { detail: EventDetail; calendar: CalendarEntry | null; places: boolean }) {
  const { t, i18n } = useTranslation()
  const { twelve } = useDisplay()
  // With "read" and "busy only" the reminders are still the member's own, and change at once (TIME-7 AC-9).
  const [leads, setLeads] = useState(detail.reminders?.minutes ?? [])
  const [leadProblem, setLeadProblem] = useState('')
  const saving = useRef(false)
  const choose = async (minutes: number[]) => {
    if (saving.current) return
    saving.current = true
    setLeadProblem('')
    try {
      const answer = await remindersApi.forEvent(detail.id, minutes)
      setLeads(answer.reminders?.minutes ?? minutes)
    } catch (error) {
      setLeadProblem(problemText(error))
    } finally {
      saving.current = false
    }
  }
  const when = detail.all_day
    ? `${dayName(detail.start.slice(0, 10), i18n.language)} · ${t('rule.allDay')}`
    : `${dayName(detail.start.slice(0, 10), i18n.language)} · ${clockText(detail.start.slice(11, 16), i18n.language, twelve)}–${clockText(detail.end.slice(11, 16), i18n.language, twelve)}`
  const owner = calendar?.owner ?? ''
  const reason = detail.busy_only ? t('panel.readOnly.busy', { owner }) : t('panel.readOnly.read', { name: calendar?.name ?? '' })
  return (
    <div className="space-y-3" data-testid="read-only">
      <strong className="block text-lg text-mist-100">{detail.busy_only ? t('panel.busyBlock') : detail.title}</strong>
      <span className="block text-sm text-mist-500">{when}</span>
      {detail.recurring && <span className="block text-xs text-mist-500">{detail.rule ? ruleSentence(detail.rule, detail.series_start.start.slice(0, 10), null, t, i18n.language) : rruleSentence(detail.rrule, detail.series_start.start.slice(0, 10), t, i18n.language)}</span>}
      <People detail={detail} />
      {detail.waiting && (
        <span className="nt-waiting" data-testid="waiting">
          {t('views.waiting')}
        </span>
      )}
      {detail.notes && (
        <p className="text-sm break-words whitespace-pre-wrap text-mist-200">
          <LinkedText text={detail.notes} />
        </p>
      )}
      {joinLink(detail.notes) && (
        <a href={joinLink(detail.notes) ?? undefined} target="_blank" rel="noopener noreferrer" className="nt-btn h-9 bg-[var(--color-accent-wash)] text-accent-400">
          {t('panel.join')}
        </a>
      )}
      {!detail.busy_only && detail.location && <PlaceView location={detail.location} point={detail.place ?? null} enabled={places} />}
      <ReminderChips minutes={leads} presets={detail.all_day ? ALL_DAY_PRESETS : PRESETS} allDay={detail.all_day} onChange={(minutes) => void choose(minutes)} note={withOccurrences(detail.shared && leads.length ? t('reminders.ownOnly') : undefined, detail.reminders, t)} />
      {leadProblem && (
        <p role="alert" className="text-xs text-bad-500">
          {leadProblem}
        </p>
      )}
      {!detail.busy_only && (detail.attachments?.length ?? 0) > 0 && (
        <Attachments target={{ event: detail.id }} items={detail.attachments ?? emptyFiles} allowed={false} maxMb={detail.attachment_max_mb ?? 25} />
      )}
      {!detail.busy_only && (detail.remote_attachments?.length ?? 0) > 0 && (
        <RemoteAttachments eventId={detail.id} items={detail.remote_attachments ?? []} source={detail.source ?? ''} />
      )}
      <Attendees detail={detail} />
      {detail.no_answers && (
        <p className="text-xs text-mist-500" data-testid="no-answers">
          {t('invitations.whyNot.read_only_source')}
        </p>
      )}
      <Note tone="plain">
        <span data-testid="read-only-reason">{reason}</span>
      </Note>
    </div>
  )
}

function Conflict({ detail, onRestore, onLeave }: { detail: EventDetail; onRestore: () => void; onLeave: () => void }) {
  const { t, i18n } = useTranslation()
  const { twelve } = useDisplay()
  const clash = detail.conflict
  if (!clash) return null
  const line = (summary: Summary) =>
    `${summary.title} · ${dayName(summary.start.slice(0, 10), i18n.language)}${summary.all_day ? '' : ` ${clockText(summary.start.slice(11, 16), i18n.language, twelve)}–${clockText(summary.end.slice(11, 16), i18n.language, twelve)}`}`
  const name = clash.by?.name ?? ''
  return (
    <div role="status" className="space-y-2 rounded-[12px] border border-warn-500/40 bg-warn-wash p-3 text-sm text-mist-100" data-testid="conflict">
      <p>{clash.self ? t('conflict.self') : t('conflict.text', { name })}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-mist-500">{t('conflict.mine')}</dt>
        <dd className="line-through">{line(clash.mine)}</dd>
        <dt className="text-mist-500">{clash.self ? t('conflict.now') : t('conflict.theirs', { name })}</dt>
        <dd>{line(clash.theirs)}</dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="nt-btn nt-btn-ghost h-9" onClick={onRestore} data-testid="conflict-restore">
          {t('conflict.restore')}
        </button>
        <button type="button" className="nt-btn nt-btn-accent h-9" onClick={onLeave} data-testid="conflict-leave">
          {t('conflict.leave')}
        </button>
      </div>
    </div>
  )
}

