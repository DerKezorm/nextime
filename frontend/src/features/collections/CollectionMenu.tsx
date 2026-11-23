/**
 * Switching collections (TIME-13): the button in the sidebar ("Sammlung Familie ▾") and in the head on a phone
 * ("Alles ▾") opens the menu of all collections, the chosen one ticked, then "Sammlung „…“ bearbeiten …",
 * "Neue Sammlung …" and the note that the choice holds on this device only (AC-6). The dialog has the name, the
 * calendars of every source, "Sammlung löschen", "Abbrechen" and "Sichern"; only "Sichern" takes the change (AC-7).
 * Also here: the bar while one calendar is shown alone (AC-12) and the note of an empty collection (EC-1).
 */
import { ListChecks } from 'lucide-react'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { Menu, MenuChoice, MenuLine } from '../../components/Menu'
import { errorText } from '../../lib/errors'
import { collectionsApi, newRef, type CalendarEntry, type Collection } from '../calendars/api'
import { announce, useCalendars } from '../calendars/store'
import { Swatch } from '../calendars/Swatch'
import { useChosen, useCollections, useOnly, useShown } from './store'
import { useOffline } from '../offline/connection'
import { useNextasks, useTaskAreas } from '../nextasks/hooks'
import { AreaMark } from '../nextasks/AreaMark'
import { DEADLINES_ROW } from '../nexpaper/api'

const NAME_MAX = 60

export function CollectionButton({ compact = false, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  const { t } = useTranslation()
  const { list } = useCollections()
  const { chosen, choose } = useChosen()
  const [anchor, setAnchor] = useState<HTMLElement | null>(null)
  const [editing, setEditing] = useState<{ collection: Collection | null } | null>(null)
  // Collections are kept on the server: without a connection they are chosen, not made or changed (TIME-28 AC-3).
  const offline = useOffline()
  const name = chosen ? chosen.name : t('collections.all')
  const pick = (id: number | 'all') => {
    setAnchor(null)
    choose(id)
    onNavigate?.()
  }
  return (
    <>
      <button
        type="button"
        className={
          compact
            ? 'inline-flex min-w-0 items-center gap-1 text-sm font-semibold text-mist-100'
            : 'flex h-[34px] w-full items-center justify-between gap-2 rounded-lg px-2.5 text-[13.5px] ' + (anchor ? 'bg-accent-wash' : 'hover:bg-ink-800')
        }
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        onClick={(event) => setAnchor(event.currentTarget)}
        data-testid={compact ? 'collection-button-compact' : 'collection-button'}
      >
        {!compact && <span className="text-mist-500">{t('collections.label')}</span>}
        <strong className="min-w-0 truncate font-semibold text-mist-100" title={name}>
          {name} ▾
        </strong>
      </button>
      {anchor && (
        <Menu anchor={anchor} onClose={() => setAnchor(null)} label={t('collections.label')} align="start" width={260} testId="collection-menu">
          <div className="nt-scroll max-h-[min(60vh,420px)] overflow-y-auto">
            <MenuChoice checked={!chosen} onPick={() => pick('all')} testId="collection-choice">
              {t('collections.all')}
            </MenuChoice>
            {(list ?? []).map((entry) => (
              <MenuChoice key={entry.id} checked={chosen?.id === entry.id} onPick={() => pick(entry.id)} testId="collection-choice">
                {entry.name}
              </MenuChoice>
            ))}
          </div>
          <MenuLine />
          {chosen && (
            <button type="button" role="menuitem" className="nt-menu-item text-accent-400" disabled={offline} onClick={() => (setAnchor(null), setEditing({ collection: chosen }))} data-testid="collection-edit">
              {t('collections.edit', { name: chosen.name })}
            </button>
          )}
          <button type="button" role="menuitem" className="nt-menu-item text-accent-400" disabled={offline} onClick={() => (setAnchor(null), setEditing({ collection: null }))} data-testid="collection-new">
            {t('collections.new')}
          </button>
          <p className="px-2.5 py-1 text-xs text-mist-500">{t('collections.deviceOnly')}</p>
        </Menu>
      )}
      {editing && <CollectionDialog collection={editing.collection} onClose={() => setEditing(null)} onSaved={(id) => (setEditing(null), id !== null ? choose(id) : choose('all'))} />}
    </>
  )
}

/** Where a calendar comes from, as the list of the dialog names it (AC-2). */
function sourceOf(entry: CalendarEntry, t: (key: string) => string): string {
  if (entry.right === 'busy') return t('collections.sources.sharedBusy')
  if (entry.background) return t('collections.sources.background')
  if (entry.group === 'shared') return t('collections.sources.shared')
  return t(`collections.sources.${entry.group}`)
}

/**
 * A new collection or one to change (AC-2, AC-7). Blanks at the edges go, an empty, too long or doubled name says so at
 * the field (EC-6, EC-15); a second click saves once (EC-10); a broken connection keeps everything open (EC-11).
 */
export function CollectionDialog({ collection, onClose, onSaved }: { collection: Collection | null; onClose: () => void; onSaved: (id: number | null) => void }) {
  const { t } = useTranslation()
  const { list } = useCalendars()
  const { list: collections, take } = useCollections()
  const [name, setName] = useState(collection?.name ?? '')
  const [chosen, setChosen] = useState<number[]>(collection?.calendar_ids ?? [])
  const [taskAreas, setTaskAreas] = useState<string[]>(areasOf(collection))
  const nextasks = useNextasks()
  const known = useTaskAreas()
  /** One area on or off; "every area" turns into the areas known first. */
  const pickArea = (id: string, on: boolean) => {
    const before = taskAreas.includes('*') ? known.map((area) => area.id) : taskAreas
    setTaskAreas(on ? [...before.filter((one) => one !== id), id] : before.filter((one) => one !== id))
  }
  const [problem, setProblem] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef(newRef())
  const inFlight = useRef(false)

  const save = async () => {
    if (inFlight.current) return
    const clean = name.trim()
    const twin = (collections ?? []).find((entry) => entry.id !== collection?.id && entry.name.toLocaleLowerCase() === clean.toLocaleLowerCase())
    const found = !clean ? t('collections.nameMissing') : clean.length > NAME_MAX ? t('collections.tooLong', { max: NAME_MAX, count: clean.length }) : twin ? t('collections.taken', { name: clean }) : null
    setProblem(found)
    setMessage(null)
    if (found) return
    inFlight.current = true
    setBusy(true)
    try {
      const answer = collection ? await collectionsApi.change(collection.id, clean, chosen, taskAreas) : await collectionsApi.create(clean, chosen, ref.current, taskAreas)
      take(answer.collections)
      announce()
      onSaved(collection ? collection.id : (answer.created ?? null))
    } catch (error) {
      if (error instanceof ApiError && error.values.field === 'name') setProblem(errorText(error.code, error.values))
      else if (error instanceof ApiError && error.code === 'collection_gone') setMessage(errorText(error.code, error.values))
      else setMessage(t('collections.notSaved'))
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!collection) return
    setBusy(true)
    try {
      const answer = await collectionsApi.remove(collection.id)
      take(answer.collections)
      announce()
      onSaved(null)
    } catch {
      setDeleting(false)
      setMessage(t('collections.notSaved'))
    } finally {
      setBusy(false)
    }
  }

  if (deleting && collection)
    return (
      <Dialog title={t('collections.deleteTitle', { name: collection.name })} onClose={() => setDeleting(false)}>
        <p className="text-sm text-mist-200" data-testid="collection-delete-text">
          {t('collections.deleteText')}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setDeleting(false)}>
            {t('common.cancel')}
          </button>
          <button type="button" className="nt-btn nt-btn-danger" disabled={busy} onClick={() => void remove()} data-testid="collection-delete-confirm">
            {t('collections.deleteButton')}
          </button>
        </div>
      </Dialog>
    )

  const calendars = list?.calendars ?? []
  return (
    <Dialog title={collection ? t('collections.title', { name: collection.name }) : t('collections.newTitle')} onClose={onClose}>
      <form
        className="space-y-4"
        data-testid="collection-dialog"
        onSubmit={(event) => {
          event.preventDefault()
          void save()
        }}
      >
        <label className="block text-sm">
          <span className="font-medium text-mist-100">{t('collections.name')}</span>
          <input className={'nt-field mt-1 ' + (problem ? 'border-bad-500' : '')} value={name} aria-invalid={problem ? true : undefined} onChange={(event) => setName(event.target.value)} data-testid="collection-name" />
          {problem && (
            <span role="alert" className="mt-1 block text-xs text-bad-500" data-testid="collection-name-problem">
              {problem}
            </span>
          )}
        </label>
        <fieldset className="space-y-2">
          <legend className="nt-legend">{t('collections.calendars')}</legend>
          <div className="nt-scroll max-h-[340px] space-y-2 overflow-y-auto pr-1" data-testid="collection-calendars">
            {calendars.map((entry) => (
              <label key={entry.id} className="flex items-center gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="h-[18px] w-[18px] accent-[var(--color-accent-500)]"
                  checked={chosen.includes(entry.id)}
                  onChange={(event) => setChosen(event.target.checked ? [...chosen, entry.id] : chosen.filter((id) => id !== entry.id))}
                  data-testid="collection-calendar"
                />
                <Swatch color={entry.color} icon={entry.icon} />
                <span className="nt-wrap min-w-0 flex-1 text-mist-100">{entry.name}</span>
                <span className="shrink-0 text-xs text-mist-500">{sourceOf(entry, t)}</span>
              </label>
            ))}
            {nextasks?.connected &&
              known.map((area) => (
                <label key={area.id} className="flex items-center gap-2.5 text-sm">
                  <input
                    type="checkbox"
                    className="h-[18px] w-[18px] accent-[var(--color-accent-500)]"
                    checked={taskAreas.includes('*') || taskAreas.includes(area.id)}
                    onChange={(event) => pickArea(area.id, event.target.checked)}
                    data-testid="collection-area"
                  />
                  <AreaMark area={area} />
                  <span className="nt-wrap min-w-0 flex-1 text-mist-100">
                    {area.name}
                    {area.area_name && <span className="text-mist-500"> · {area.area_name}</span>}
                  </span>
                  <span className="shrink-0 text-xs text-mist-500">{t('tasks.source')}</span>
                </label>
              ))}
            {nextasks?.connected && !known.length && (
              <label className="flex items-center gap-2.5 text-sm">
                <input
                  type="checkbox"
                  className="h-[18px] w-[18px] accent-[var(--color-accent-500)]"
                  checked={taskAreas.length > 0}
                  onChange={(event) => setTaskAreas(event.target.checked ? ['*'] : [])}
                  data-testid="collection-tasks"
                />
                <ListChecks className="h-[14px] w-[14px] shrink-0 text-mist-400" aria-hidden />
                <span className="nt-wrap min-w-0 flex-1 text-mist-100">{t('tasks.mine')}</span>
                <span className="shrink-0 text-xs text-mist-500">{t('tasks.source')}</span>
              </label>
            )}
          </div>
        </fieldset>
        {message && (
          <p role="alert" className="text-sm text-bad-500" data-testid="collection-message">
            {message}
          </p>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          {collection && (
            <button type="button" className="nt-btn nt-btn-quiet mr-auto text-bad-500" onClick={() => setDeleting(true)} data-testid="collection-delete">
              {t('collections.delete')}
            </button>
          )}
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" disabled={busy} data-testid="collection-save">
            {t('collections.save')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

/** "Nur Familie · andere Kalender vorübergehend ausgeblendet" with "Vorherige Auswahl zeigen" (AC-12). */
export function OnlyBar() {
  const { t } = useTranslation()
  const shown = useShown()
  const { setOnly, setOnlyArea } = useOnly()
  const name = shown.only?.name ?? (shown.onlyArea?.id === DEADLINES_ROW ? t('deadlines.mine') : shown.onlyArea?.name)
  if (!shown.only && !shown.onlyArea) return null
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-ink-700 bg-[var(--color-accent-wash)] px-4 py-2 text-sm text-mist-200" data-testid="only-bar">
      <span className="min-w-0 flex-1">
        {t('collections.onlyStart')}
        <strong className="nt-wrap">{name}</strong>
        {t('collections.onlyEnd')}
      </span>
      <button type="button" className="nt-btn nt-btn-quiet text-sm" onClick={() => (shown.onlyArea ? setOnlyArea(null) : setOnly(null))} data-testid="only-end">
        {t('collections.previous')}
      </button>
    </div>
  )
}

/** A collection with no calendar to show says so calmly (EC-1), with the way to change it. */
export function EmptyCollection() {
  const { t } = useTranslation()
  const { chosen, choose } = useChosen()
  const [editing, setEditing] = useState(false)
  const offline = useOffline()
  if (!chosen) return null
  return (
    <div className="nt-empty py-8" data-testid="empty-collection">
      <p>{t('collections.empty', { name: chosen.name })}</p>
      <button type="button" className="nt-btn nt-btn-quiet text-sm" disabled={offline} onClick={() => setEditing(true)}>
        {t('collections.edit', { name: chosen.name })}
      </button>
      {editing && <CollectionDialog collection={chosen} onClose={() => setEditing(false)} onSaved={(id) => (setEditing(false), id === null && choose('all'))} />}
    </div>
  )
}

/** The areas of nextasks in a collection; one saved before areas with its tasks holds every area. */
function areasOf(collection: Collection | null): string[] {
  if (!collection) return []
  return collection.task_areas ?? (collection.with_tasks ? ['*'] : [])
}
