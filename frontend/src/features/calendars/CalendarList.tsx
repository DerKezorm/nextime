/**
 * The calendars in the sidebar (TIME-4 AC-9): grouped by where they come from (iCloud, Google, one group per CalDAV
 * account with its name, "Abos und Hintergrund", TIME-15 AC-9, TIME-17 AC-6), a group without calendars not shown
 * (EC-12, TIME-15 EC-9); each with its color, its name (shortened, in full as a tooltip, EC-5) and a quiet note ("shared", "busy").
 * A click opens the month with this calendar alone, as a folder of nexnotes shows its notes (TIME-13 "Nur diesen
 * zeigen", decided while testing, 2026-11-23); the row is chosen then, and a second click (or "Vorherige Auswahl zeigen")
 * brings back what was before, the view staying. Alt+click does the same. Showing and hiding (TIME-4 AC-3) is the eye
 * beside "…": on hovering, always for a hidden calendar. Its menu edits, moves within the group (AC-5), makes it the
 * default or deletes it.
 * "Teilen …" for the owner of an own calendar or one from iCloud, Google, CalDAV (TIME-19), "Einstellungen und
 * Mitteilungen" for every calendar (TIME-21); a calendar shared with the member shows its owner's name (TIME-19 AC-1).
 */
import { ArrowDown, ArrowUp, Bell, CircleMinus, Eye, EyeOff, MoreHorizontal, Pencil, Plus, Share2, Star, Trash2, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'

import { Menu, MenuEntry, type MenuAnchor } from '../../components/Menu'
import { Box, Button, Buttons, Row, Rows } from '../../vendor/nexui'
import { problemText } from '../../lib/errors'
import { showNotice } from '../../lib/notice'
import { trashApi } from '../trash/api'
import { calendarsApi, GROUPS, type CalendarEntry, type CalendarList as List } from './api'
import { AddCalendar } from '../sources/AddCalendar'
import { shareKind } from '../shares/api'
import { ShareDialog } from '../shares/ShareDialog'
import { CalendarDialog, DeleteCalendarDialog, LeaveCalendarDialog } from './CalendarDialogs'
import { Swatch } from './Swatch'
import { NexpaperGroup } from '../nexpaper/NexpaperGroup'
import { NextasksGroup } from '../nextasks/NextasksGroup'
import { useOnly } from '../collections/store'
import { announce, useCalendars } from './store'
import { useOffline } from '../offline/connection'
import { afterAlone } from '../views/state'

/** A head of the list, as nexnotes' "ORDNER": 11 px, semibold, in capitals. */
const HEAD = 'mt-3 flex items-center justify-between gap-2 rounded-lg pr-1 pl-2.5'
const HEAD_TEXT = 'py-1 text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase'

/**
 * `settings`: the list under Settings › "Kalender und Konten" (TIME-4, decided while testing, 2026-11-23): every calendar by
 * group as in the sidebar, rows in a card as nexnotes' settings lists have them, the marks always to read ("Standard",
 * "ausgeblendet", "geteilt"), the eye and "…" always there; a click on a row opens what there is to set (its dialog, a
 * calendar of others its page). The "+" stays at the top of the page there.
 */
export function CalendarList({ onNavigate, settings = false }: { onNavigate?: () => void; settings?: boolean }) {
  const { t } = useTranslation()
  const { list, failed, take } = useCalendars()
  const [dialog, setDialog] = useState<{ kind: 'add' } | { kind: 'edit' | 'delete' | 'share' | 'leave'; calendar: CalendarEntry } | null>(null)
  const navigate = useNavigate()
  const location = useLocation()
  const [menu, setMenu] = useState<{ anchor: MenuAnchor; calendar: CalendarEntry } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const { only, setOnly } = useOnly()
  // Without a connection nothing is added (TIME-28 AC-3).
  const offline = useOffline()

  /** The calendar alone, in the view on screen (from elsewhere in Month); the same again brings back the choice before (TIME-13). */
  const choose = (entry: CalendarEntry) => {
    if (only === entry.id) return setOnly(null)
    setOnly(entry.id)
    const next = afterAlone(location.pathname)
    if (next) navigate(next)
    onNavigate?.()
  }

  /** The eye (TIME-4 AC-3): shown or hidden, kept on the server; without a connection it waits (TIME-28 AC-3). */
  const toggle = (entry: CalendarEntry) => {
    if (offline) return setProblem(t('offline.changeOff'))
    void act(() => calendarsApi.change(entry.id, { hidden: !entry.hidden }))
  }

  const act = async (work: () => Promise<List>) => {
    setProblem(null)
    try {
      take(await work())
    } catch (error) {
      setProblem(problemText(error))
    }
  }

  /** In the trash now (TIME-23): "Undo" brings it back with its events, as nexnotes does with a folder. */
  const deleted = (calendar: CalendarEntry) =>
    showNotice({
      text: t('calendars.deleted', { name: calendar.name }),
      undo: async () => {
        await trashApi.restore({ kind: 'calendar', id: calendar.id })
        announce()
        return t('trash.restored', { title: calendar.name })
      },
    })

  const move = (calendar: CalendarEntry, step: -1 | 1) => {
    if (!list) return
    const group = list.calendars.filter((entry) => entry.group === calendar.group).map((entry) => entry.id)
    const at = group.indexOf(calendar.id)
    const to = at + step
    if (to < 0 || to >= group.length) return
    ;[group[at], group[to]] = [group[to], group[at]]
    void act(() => calendarsApi.order(group))
  }

  const openMenu = (anchor: MenuAnchor, calendar: CalendarEntry) => setMenu({ anchor, calendar })
  const { groups, partners } = list ? groupsOf(list.calendars, t) : { groups: [], partners: [] }
  // In the settings a plain block among the cards (a card there is a section with its head).
  const Frame = settings ? 'div' : 'section'
  /** In the settings a click opens what there is to set: its dialog, a calendar of others its page. */
  const settle = (entry: CalendarEntry) => (entry.right === 'owner' ? setDialog({ kind: 'edit', calendar: entry }) : navigate(`/calendar/${entry.id}`))
  // The "+" of the list stands in the head of its first group, as nexnotes' in the head of its folders; without any
  // calendar a head of its own carries it.
  const addButton = (
    <button
      type="button"
      disabled={offline}
      onClick={() => setDialog({ kind: 'add' })}
      aria-label={t('calendars.add')}
      title={t('calendars.add')}
      className="rounded-md p-1 text-mist-500 hover:bg-ink-800 hover:text-mist-100 disabled:cursor-not-allowed disabled:opacity-50"
      data-testid="add-calendar"
    >
      <Plus aria-hidden className="h-3.5 w-3.5" strokeWidth={2.2} />
    </button>
  )

  /** Settings › Kalender und Konten (plan "Einstellungen", E39 and the gallery): a group as a box of the kit, every
   * calendar a row with its colour, its marks under the name and the kit's buttons: "Bearbeiten" (its dialog; a
   * calendar of others: "Öffnen", its page), "Ausblenden"/"Einblenden" (the eye) and "Mehr" (the menu). */
  const settingsGroup = (key: string, group: string, label: string | null, entries: CalendarEntry[]) => (
    <div key={key} data-testid={`calendar-group-${group}`}>
      <Box title={label ?? t(`calendars.groups.${group}`)}>
        <Rows>
          {entries.map((entry) => {
            const note = marks(entry, list?.default_id ?? null, t)
            const open = menu?.calendar.id === entry.id
            return (
              <Row
                key={entry.id}
                lead={<Swatch color={entry.color} icon={entry.icon} hollow={entry.hidden} />}
                name={<span data-testid="calendar-name">{entry.name}</span>}
                sub={note ? <span data-testid="calendar-marks">{note}</span> : undefined}
                action={
                  <Buttons>
                    <Button
                      kind="secondary"
                      size="sm"
                      icon={entry.right === 'owner' ? 'Pencil' : 'ArrowRight'}
                      onClick={() => settle(entry)}
                      aria-label={`${entry.name}: ${entry.right === 'owner' ? t('calendars.settingsRow.edit') : t('calendars.settingsRow.open')}`}
                      testId="settings-calendar"
                    >
                      {entry.right === 'owner' ? t('calendars.settingsRow.edit') : t('calendars.settingsRow.open')}
                    </Button>
                    <Button
                      kind="secondary"
                      size="sm"
                      icon={entry.hidden ? Eye : EyeOff}
                      disabled={offline}
                      aria-pressed={!entry.hidden}
                      aria-label={entry.hidden ? t('calendars.showNamed', { name: entry.name }) : t('calendars.hideNamed', { name: entry.name })}
                      onClick={() => toggle(entry)}
                      testId="calendar-eye"
                    >
                      {entry.hidden ? t('calendars.settingsRow.show') : t('calendars.settingsRow.hide')}
                    </Button>
                    <Button
                      kind="secondary"
                      size="sm"
                      icon={MoreHorizontal}
                      aria-label={t('calendars.more', { name: entry.name })}
                      aria-haspopup="menu"
                      aria-expanded={open}
                      onClick={(event) => openMenu(event.currentTarget, entry)}
                    >
                      {t('calendars.settingsRow.more')}
                    </Button>
                  </Buttons>
                }
              />
            )
          })}
        </Rows>
      </Box>
    </div>
  )

  /** A group of the sidebar: its head (the first one with the "+") and a row per calendar. */
  const sidebarGroup = ({ key, group, label, entries }: GroupRow, index: number) => (
        <div key={key} data-testid={`calendar-group-${group}`}>
          <div className={HEAD}>
            <h3 className={HEAD_TEXT + ' min-w-0 truncate'} title={label ?? undefined}>
              {label ?? t(`calendars.groups.${group}`)}
            </h3>
            {index === 0 && !settings && addButton}
          </div>
          <ul className={settings ? 'divide-y divide-line overflow-hidden rounded-[12px] border border-ink-700 bg-ink-850' : undefined}>
            {entries.map((entry) => {
              const note = settings ? marks(entry, list?.default_id ?? null, t) : extra(entry, t)
              const open = menu?.calendar.id === entry.id
              const alone = only === entry.id
              return (
                <li
                  key={entry.id}
                  className={
                    settings
                      ? 'group relative flex h-11 items-center gap-1.5 pr-2 text-sm ' + (open ? 'bg-ink-800' : 'hover:bg-ink-800')
                      : 'group relative flex h-8 items-center gap-1.5 rounded-lg pr-1 text-[13.5px] pointer-coarse:h-11 ' +
                        (alone ? 'nt-chosen ' : open ? 'bg-ink-800' : 'hover:bg-ink-800')
                  }
                  data-chosen={alone || undefined}
                  onContextMenu={(event) => {
                    event.preventDefault()
                    openMenu({ x: event.clientX, y: event.clientY }, entry)
                  }}
                >
                  <button
                    type="button"
                    aria-pressed={alone}
                    title={`${entry.name}: ${t('calendars.openAlone')}`}
                    className={
                      'flex h-full min-w-0 flex-1 items-center gap-2.5 rounded-lg pl-2.5 text-left outline-offset-[-2px] ' +
                      (alone ? 'font-semibold' : 'text-mist-100')
                    }
                    onClick={() => (settings ? settle(entry) : choose(entry))}
                    data-testid={settings ? 'settings-calendar' : 'calendar-toggle'}
                  >
                    <Swatch color={entry.color} icon={entry.icon} hollow={entry.hidden} />
                    <span className={'min-w-0 flex-1 truncate ' + (entry.hidden ? 'text-mist-500' : '')} data-testid="calendar-name">
                      {entry.name}
                    </span>
                    {note && (
                      <span
                        className={
                          'max-w-[45%] shrink-0 truncate text-xs text-mist-500 ' +
                          (settings ? '' : open ? 'hidden pointer-coarse:inline' : 'group-focus-within:hidden group-hover:hidden pointer-coarse:inline')
                        }
                        data-testid="calendar-marks"
                      >
                        {note}
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    aria-label={entry.hidden ? t('calendars.showNamed', { name: entry.name }) : t('calendars.hideNamed', { name: entry.name })}
                    aria-pressed={!entry.hidden}
                    disabled={offline}
                    className={
                      'h-6 w-6 shrink-0 items-center justify-center rounded p-0.5 text-mist-400 hover:bg-ink-700 hover:text-mist-100 disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:flex ' +
                      (entry.hidden || settings ? 'flex' : open ? 'flex' : 'hidden group-focus-within:flex group-hover:flex')
                    }
                    onClick={() => toggle(entry)}
                    data-testid="calendar-eye"
                  >
                    {entry.hidden ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
                  </button>
                  <button
                    type="button"
                    aria-label={t('calendars.more', { name: entry.name })}
                    aria-haspopup="menu"
                    aria-expanded={open}
                    className={
                      'shrink-0 rounded p-0.5 hover:bg-ink-700 hover:text-mist-100 pointer-coarse:block ' +
                      (open
                        ? 'block bg-accent-wash text-accent-400'
                        : settings
                          ? 'block text-mist-400'
                          : 'hidden text-mist-400 group-focus-within:block group-hover:block')
                    }
                    onClick={(event) => openMenu(event.currentTarget, entry)}
                  >
                    <MoreHorizontal className="h-4 w-4" aria-hidden />
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
  )

  return (
    <Frame
      aria-labelledby={settings ? undefined : 'calendars-title'}
      aria-label={settings ? t('nav.calendars') : undefined}
      className="pb-1"
      data-testid={settings ? 'settings-calendars' : 'calendar-list'}
    >
      {!settings && (
        <h2 id="calendars-title" className="sr-only">
          {t('nav.calendars')}
        </h2>
      )}
      {!settings && groups.length === 0 && partners.length === 0 && (
        <div className={HEAD}>
          <span className={HEAD_TEXT} aria-hidden>
            {t('nav.calendars')}
          </span>
          {addButton}
        </div>
      )}
      {!list && !failed && <div className="mx-2 h-16 animate-pulse rounded-[9px] bg-ink-800" aria-hidden />}
      {list && list.calendars.length === 0 && <p className="px-2.5 py-2 text-xs text-mist-500">{t('nav.noCalendars')}</p>}
      {settings
        ? [...groups, ...partners].map(({ key, group, label, entries }) => settingsGroup(key, group, label, entries))
        : groups.map(sidebarGroup)}
      {!settings && <NextasksGroup onNavigate={onNavigate} />}
      {!settings && <NexpaperGroup onNavigate={onNavigate} />}
      {!settings && partners.map((row) => sidebarGroup(row, -1))}
      {problem && (
        <p role="alert" className="px-2.5 text-xs text-bad-500">
          {problem}
        </p>
      )}
      {menu && (
        <Menu anchor={menu.anchor} onClose={() => setMenu(null)} label={t('calendars.more', { name: menu.calendar.name })} testId="calendar-menu">
          <MenuEntry
            icon={only === menu.calendar.id ? Undo2 : Eye}
            onClick={() => (setOnly(only === menu.calendar.id ? null : menu.calendar.id), setMenu(null))}
            testId="calendar-only"
          >
            {only === menu.calendar.id ? t('collections.previous') : t('collections.onlyThis')}
          </MenuEntry>
          {menu.calendar.right === 'owner' && (
            <MenuEntry
              icon={Pencil}
              disabled={offline}
              onClick={() => (setDialog({ kind: 'edit', calendar: menu.calendar }), setMenu(null))}
              testId="calendar-edit"
            >
              {t('calendars.edit')}
            </MenuEntry>
          )}
          {shareKind(menu.calendar.right, menu.calendar.origin) && (
            <MenuEntry
              icon={Share2}
              disabled={offline}
              onClick={() => (setDialog({ kind: 'share', calendar: menu.calendar }), setMenu(null))}
              testId="calendar-share"
            >
              {t('shares.menu')}
            </MenuEntry>
          )}
          <MenuEntry icon={Bell} onClick={() => (navigate(`/calendar/${menu.calendar.id}`), setMenu(null))} testId="calendar-settings-entry">
            {t('notify.menu')}
          </MenuEntry>
          <MenuEntry icon={ArrowUp} disabled={offline} onClick={() => (move(menu.calendar, -1), setMenu(null))} testId="calendar-up">
            {t('calendars.up')}
          </MenuEntry>
          <MenuEntry icon={ArrowDown} disabled={offline} onClick={() => (move(menu.calendar, 1), setMenu(null))} testId="calendar-down">
            {t('calendars.down')}
          </MenuEntry>
          {menu.calendar.writable && !menu.calendar.default && (
            <MenuEntry
              icon={Star}
              disabled={offline}
              onClick={() => (void act(() => calendarsApi.change(menu.calendar.id, { default: true })), setMenu(null))}
              testId="calendar-default"
            >
              {t('calendars.makeDefault')}
            </MenuEntry>
          )}
          {menu.calendar.origin && ['icloud', 'google', 'caldav'].includes(menu.calendar.origin.kind) && (
            <MenuEntry
              icon={CircleMinus}
              danger
              disabled={offline}
              onClick={() => (setDialog({ kind: 'leave', calendar: menu.calendar }), setMenu(null))}
              testId="calendar-leave"
            >
              {t('calendars.leave.menu')}
            </MenuEntry>
          )}
          {menu.calendar.right === 'owner' && !menu.calendar.origin && (
            <MenuEntry
              icon={Trash2}
              danger
              disabled={offline}
              onClick={() => (setDialog({ kind: 'delete', calendar: menu.calendar }), setMenu(null))}
              testId="calendar-delete"
            >
              {t('calendars.delete')}
            </MenuEntry>
          )}
        </Menu>
      )}
      {dialog?.kind === 'add' && <AddCalendar onClose={() => setDialog(null)} />}
      {dialog?.kind === 'edit' && (
        <CalendarDialog
          calendar={dialog.calendar}
          onDone={(answer) => (take(answer), setDialog(null))}
          onDelete={dialog.calendar.origin ? undefined : () => setDialog({ kind: 'delete', calendar: dialog.calendar })}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'share' && (
        <ShareDialog calendarId={dialog.calendar.id} name={dialog.calendar.name} color={dialog.calendar.color} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === 'leave' && (
        <LeaveCalendarDialog
          calendar={dialog.calendar}
          onDone={() => {
            setDialog(null)
            announce()
            showNotice({ text: t('calendars.leave.done', { name: dialog.calendar.name }) })
          }}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.kind === 'delete' && (
        <DeleteCalendarDialog
          calendar={dialog.calendar}
          onDone={(answer) => (take(answer), setDialog(null), deleted(dialog.calendar))}
          onClose={() => setDialog(null)}
        />
      )}
    </Frame>
  )
}

/** The marks of a row in the settings: default, hidden, shared, or whose it is. */
function marks(entry: CalendarEntry, defaultId: number | null, t: (key: string) => string): string {
  const found = [entry.id === defaultId ? t('calendars.marks.default') : '', entry.hidden ? t('calendars.marks.hidden') : '', extra(entry, t)].filter(Boolean)
  return found.join(' · ')
}

/** The quiet note beside a name: shared by me, the owner of one shared with me, or "busy" for one shared as busy only. */
function extra(entry: CalendarEntry, t: (key: string) => string): string {
  if (entry.right === 'busy') return t('calendars.busyOnly')
  if (entry.right !== 'owner' && entry.owner) return entry.owner
  if (entry.shared) return t('calendars.sharedNote')
  return ''
}

type GroupRow = { key: string; group: string; label: string | null; entries: CalendarEntry[] }

/** The groups in the order of the sidebar; every CalDAV account a group of its own under its name (TIME-15 AC-9). A
 * subscription made over a connection with a nex app stands apart under "Aus <app>" (`partners`), after the groups
 * of nextasks and nexpaper, as they do. */
function groupsOf(calendars: CalendarEntry[], t: (key: string, options: { app: string }) => string): { groups: GroupRow[]; partners: GroupRow[] } {
  const out: GroupRow[] = []
  const partners = new Map<string, CalendarEntry[]>()
  for (const entry of calendars) {
    const app = entry.group === 'subscriptions' ? entry.origin?.connection : null
    if (app) partners.set(app, [...(partners.get(app) ?? []), entry])
  }
  for (const group of GROUPS) {
    const entries = calendars.filter((entry) => entry.group === group && !(group === 'subscriptions' && entry.origin?.connection))
    if (!entries.length) continue
    if (group !== 'caldav') {
      out.push({ key: group, group, label: null, entries })
      continue
    }
    const accounts = new Map<number, CalendarEntry[]>()
    for (const entry of entries) {
      const id = entry.origin?.id ?? 0
      accounts.set(id, [...(accounts.get(id) ?? []), entry])
    }
    for (const [id, own] of accounts) out.push({ key: `caldav-${id}`, group, label: own[0].origin?.label ?? null, entries: own })
  }
  return { groups: out, partners: [...partners].map(([app, entries]) => ({ key: `app-${app}`, group: app, label: t('calendars.groups.fromApp', { app }), entries })) }
}
