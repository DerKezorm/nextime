/**
 * "Teilen …" of a calendar (TIME-19), built as nexnotes' share dialog (`shares/ShareDialog.tsx` there): a field for
 * members by name or address with suggestions, a row per person with picture, name, address, the right and ×, the
 * sentence that only the owner decides, and below a line the guest link.
 *
 * - An own calendar (T19-Teilen, AC-1, AC-5, AC-9): "lesen" or "bearbeiten" per person; the guest link with "Kopieren",
 *   "Gilt" (until revoked or a last day) and "Link widerrufen", while the operator allows links (AC-7).
 * - A calendar from iCloud, Google or CalDAV (T19-Fremd, AC-2, AC-10, AC-12): where it comes from, that the other one
 *   needs no access there, per person "ganz zeigen | nur belegt", a preview "Du siehst / <Name> sieht"; no guest link.
 *
 * In the dialog of an own calendar every change goes to the server at once and the dialog shows its answer, as in
 * nexnotes, so a double click or a broken connection never leaves a half share behind (EC-15): what stands there is what
 * the server holds. "Fertig" with a name typed but nobody chosen keeps the dialog open once and says so; a second
 * "Fertig" closes it. When nobody on the server goes by what was typed, the operator is offered "Person einladen"
 * (Settings › Accounts, the dialog open), as in nexnotes. The dialog of a calendar from a source keeps its changes
 * until "Teilen" and "Abbrechen" drops them (AC-10).
 */
import { Copy, ExternalLink, Link2, UserPlus, X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { Avatar } from '../../components/Avatar'
import { Dialog } from '../../components/Dialog'
import { CheckLine } from '../../components/CheckLine'
import { Problem } from '../../components/Field'
import { copyText } from '../../lib/copy'
import { problemText } from '../../lib/errors'
import { day } from '../../lib/format'
import { useMe } from '../../state/auth'
import { announce } from '../calendars/store'
import { colorVar, Swatch } from '../calendars/Swatch'
import { dayFrom, sharesApi, type SharePerson, type ShareRight, type Sharing } from './api'

export function ShareDialog({ calendarId, name, color, onClose }: { calendarId: number; name: string; color: string; onClose: () => void }) {
  const { t } = useTranslation()
  const [sharing, setSharing] = useState<Sharing | null>(null)
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    sharesApi.of(calendarId).then(setSharing, (error) => setProblem(problemText(error)))
  }, [calendarId])

  /** One change at a time: a second click while the first is on its way does nothing (EC-15). */
  const running = useRef(false)
  const act = async (action: () => Promise<Sharing>) => {
    if (running.current) return
    running.current = true
    setBusy(true)
    setProblem('')
    try {
      setSharing(await action())
      announce()
    } catch (error) {
      setProblem(problemText(error))
      // What the server holds now, whatever happened on the way.
      sharesApi.of(calendarId).then(setSharing, () => undefined)
    } finally {
      running.current = false
      setBusy(false)
    }
  }

  const title = t('shares.dialog.title', { name: sharing?.calendar.name ?? name })
  const own = !sharing || sharing.calendar.kind === 'own'
  const search = useSearch(sharing)
  /** "Fertig" was pressed once with a name typed and nobody chosen. */
  const [warned, setWarned] = useState(false)
  const done = () => {
    if (search.query.trim() && !warned) setWarned(true)
    else onClose()
  }
  return (
    <Dialog title={title} onClose={onClose} medium lead={<Swatch color={sharing?.calendar.color ?? color} icon={sharing?.calendar.icon} />}>
      <div className="flex flex-col gap-4" data-testid="share-dialog">
        {sharing && !own && (
          <p className="text-[13px] text-mist-500" data-testid="share-origin">
            {t(`shares.origin.${sharing.calendar.kind}`)}
          </p>
        )}
        {sharing && own && <People sharing={sharing} busy={busy} act={act} search={search} warned={warned} onType={() => setWarned(false)} onClose={onClose} />}
        {sharing && !own && <SourcePeople sharing={sharing} busy={busy} act={act} search={search} onClose={onClose} />}
        {sharing && own && <GuestLinkPart sharing={sharing} busy={busy} act={act} />}
        <Problem text={problem} />
        {own && (
          <span className="flex justify-end">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={done} data-testid="share-done">
              {t('shares.dialog.done')}
            </button>
          </span>
        )}
      </div>
    </Dialog>
  )
}

type Act = (action: () => Promise<Sharing>) => Promise<void>

/** Members by name or address as the owner types, from two letters on, a moment after the last key. */
function useSearch(sharing: Sharing | null) {
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<SharePerson[]>([])
  const [searched, setSearched] = useState('')
  const asked = useRef(0)
  useEffect(() => {
    const text = query.trim()
    if (text.length < 2) {
      asked.current++
      setFound([])
      setSearched('')
      return
    }
    const mine = ++asked.current
    const timer = setTimeout(() => {
      const answer = (people: SharePerson[]) => {
        if (mine !== asked.current) return
        setFound(people)
        setSearched(text)
      }
      sharesApi.people(text).then(answer, () => answer([]))
    }, 200)
    return () => clearTimeout(timer)
  }, [query])
  const shown = new Set(sharing?.people.map((person) => person.id) ?? [])
  const offered = found.filter((person) => !shown.has(person.id))
  const typed = query.trim()
  return { query, setQuery, offered, nobody: typed.length >= 2 && searched === typed && found.length === 0 }
}

function SearchField({ search, onPick, warned = false, onType, onClose }: { search: ReturnType<typeof useSearch>; onPick: (person: SharePerson) => void; warned?: boolean; onType?: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const listId = useId()
  const field = useRef<HTMLInputElement>(null)
  const operator = useMe()?.role === 'operator'
  const type = (text: string) => {
    search.setQuery(text)
    onType?.()
  }
  return (
    <>
      <div className="relative">
        <input
          ref={field}
          className="nt-field pr-9"
          type="text"
          autoComplete="off"
          placeholder={t('shares.dialog.search')}
          aria-label={t('shares.dialog.search')}
          aria-controls={listId}
          aria-describedby={`${listId}-says`}
          value={search.query}
          data-testid="share-search"
          onChange={(event) => type(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && search.offered[0]) {
              event.preventDefault()
              onPick(search.offered[0])
            }
          }}
        />
        {search.query && (
          <button
            type="button"
            aria-label={t('shares.dialog.clear')}
            title={t('shares.dialog.clear')}
            onClick={() => {
              type('')
              field.current?.focus()
            }}
            className="absolute top-1/2 right-1.5 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-mist-500 hover:bg-ink-700 hover:text-mist-100"
          >
            <X aria-hidden className="h-3.5 w-3.5" strokeWidth={2.2} />
          </button>
        )}
        {search.offered.length > 0 && (
          <ul id={listId} role="listbox" aria-label={t('shares.dialog.found')} className="absolute top-full right-0 left-0 z-10 mt-1 overflow-hidden rounded-[10px] border border-ink-700 bg-ink-850 shadow-xl">
            {search.offered.map((person) => (
              <li key={person.id} role="option" aria-selected={false}>
                <button type="button" className="flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-ink-800" onClick={() => onPick(person)} data-testid="share-found">
                  <Avatar accountId={person.id} name={person.name} stamp={person.avatar} color={person.color} size={26} />
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate text-sm">{person.name}</span>
                    <span className="truncate text-xs text-mist-500">{person.email}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div id={`${listId}-says`} aria-live="polite" className="empty:hidden">
        {search.nobody && (
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-mist-400" data-testid="share-nobody">
            <span>{t('shares.dialog.nobody')}</span>
            {operator && (
              <Link to="/settings/accounts?invite=1" onClick={onClose} className="inline-flex items-center gap-1 text-accent-400 hover:underline" data-testid="share-invite">
                <UserPlus aria-hidden className="h-3.5 w-3.5" />
                {t('shares.dialog.invite')}
              </Link>
            )}
          </p>
        )}
        {warned && (
          <p className="text-[12.5px] font-medium text-warn-500" data-testid="share-none-chosen">
            {t('shares.dialog.noneChosen')}
          </p>
        )}
      </div>
    </>
  )
}

function PersonLine({ person, children }: { person: SharePerson; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2.5" data-testid="share-person">
      <Avatar accountId={person.id} name={person.name} stamp={person.avatar} color={person.color} size={32} />
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm" title={person.name}>
          {person.name}
        </span>
        <span className="truncate text-xs text-mist-500">{person.email}</span>
      </span>
      {children}
    </div>
  )
}

function RemoveButton({ person, busy, onClick }: { person: SharePerson; busy: boolean; onClick: () => void }) {
  const { t } = useTranslation()
  return (
    <button type="button" className="nt-btn nt-btn-ghost h-[34px] w-[34px] border-transparent px-0" aria-label={t('shares.dialog.remove', { name: person.name })} disabled={busy} onClick={onClick}>
      <X aria-hidden className="h-4 w-4" />
    </button>
  )
}

/** An own calendar: members with "lesen" or "bearbeiten" (AC-1, AC-9). */
function People({ sharing, busy, act, search, warned, onType, onClose }: { sharing: Sharing; busy: boolean; act: Act; search: ReturnType<typeof useSearch>; warned: boolean; onType: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const id = sharing.calendar.id
  const add = (person: SharePerson) => {
    search.setQuery('')
    onType()
    void act(() => sharesApi.set(id, person.id, 'read'))
  }
  return (
    <section className="flex flex-col gap-2.5">
      <p className="text-[13px] text-mist-500" data-testid="share-hint">
        {t('shares.dialog.onlyYou')}
      </p>
      <SearchField search={search} onPick={add} warned={warned} onType={onType} onClose={onClose} />
      {sharing.people.map((person) => (
        <PersonLine key={person.id} person={person}>
          <select
            className="nt-field h-[34px] w-auto py-0"
            value={person.right}
            aria-label={t('shares.dialog.right', { name: person.name })}
            disabled={busy}
            onChange={(event) => void act(() => sharesApi.set(id, person.id, event.target.value as ShareRight))}
          >
            <option value="edit">{t('shares.right.edit')}</option>
            <option value="read">{t('shares.right.read')}</option>
          </select>
          <RemoveButton person={person} busy={busy} onClick={() => void act(() => sharesApi.remove(id, person.id))} />
        </PersonLine>
      ))}
    </section>
  )
}

/** The guest link of an own calendar (AC-5, AC-7, EC-19, EC-20). */
function GuestLinkPart({ sharing, busy, act }: { sharing: Sharing; busy: boolean; act: Act }) {
  const { t, i18n } = useTranslation()
  const [copied, setCopied] = useState(false)
  const id = sharing.calendar.id
  const link = sharing.link
  if (!sharing.links_allowed)
    return (
      <section className="border-t border-ink-700 pt-3.5" data-testid="share-link-off">
        <p className="text-[12.5px] text-mist-500">{t('shares.link.off')}</p>
      </section>
    )
  return (
    <section className="flex flex-col gap-2.5 border-t border-ink-700 pt-3.5" data-testid="share-link">
      <span className="flex items-center gap-2">
        <Link2 aria-hidden className="h-4 w-4 shrink-0 text-mist-400" />
        <CheckLine
          label={t('shares.link.title')}
          checked={Boolean(link)}
          disabled={busy}
          testId="share-link-switch"
          className="text-[13px] font-semibold"
          onChange={() => void act(() => (link ? sharesApi.revokeLink(id) : sharesApi.setLink(id, null)))}
        />
      </span>
      {link && (
        <>
          {link.url ? (
            <div className="flex gap-2">
              <input className="nt-field min-w-0 flex-1 font-mono text-xs" readOnly value={link.url} aria-label={t('shares.link.address')} data-testid="share-link-url" onFocus={(event) => event.target.select()} />
              <button type="button" className="nt-btn nt-btn-accent shrink-0" onClick={() => void copyText(link.url!).then(setCopied)} data-testid="share-link-copy">
                <Copy aria-hidden className="h-4 w-4" />
                {copied ? t('shares.link.copied') : t('shares.link.copy')}
              </button>
            </div>
          ) : (
            <p className="flex flex-wrap items-center gap-2 text-xs text-mist-500" data-testid="share-link-unreadable">
              {t('shares.link.unreadable')}
              <button type="button" className="text-xs text-accent-400 hover:underline" disabled={busy} onClick={() => void act(() => sharesApi.renewLink(id))}>
                {t('shares.link.renew')}
              </button>
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2.5 text-[13px]">
            <label className="flex items-center gap-2">
              <span className="text-mist-500">{t('shares.link.valid')}</span>
              <select
                className="nt-field h-[34px] w-auto py-0"
                value={link.expires_on ? 'until' : 'revoked'}
                disabled={busy}
                data-testid="share-link-valid"
                onChange={(event) => void act(() => sharesApi.setLink(id, event.target.value === 'until' ? dayFrom(30) : null))}
              >
                <option value="revoked">{t('shares.link.untilRevoked')}</option>
                <option value="until">{t('shares.link.untilDay')}</option>
              </select>
            </label>
            {link.expires_on && (
              <input
                type="date"
                className="nt-field nt-date h-[34px] w-auto py-0"
                min={dayFrom(0)}
                required
                value={link.expires_on}
                aria-label={t('shares.link.lastDay')}
                title={day(`${link.expires_on}T12:00:00`, i18n.language)}
                data-testid="share-link-day"
                disabled={busy}
                onChange={(event) => event.target.value && void act(() => sharesApi.setLink(id, event.target.value))}
              />
            )}
            <button type="button" className="ml-auto text-[13px] text-bad-500 hover:underline" disabled={busy} onClick={() => void act(() => sharesApi.revokeLink(id))} data-testid="share-link-revoke">
              {t('shares.link.revoke')}
            </button>
          </div>
          {link.url && (
            <a href={link.url} target="_blank" rel="noreferrer" className="inline-flex w-max items-center gap-1.5 text-[13px] text-accent-400 hover:underline">
              <ExternalLink aria-hidden className="h-3.5 w-3.5" />
              {t('shares.link.view')}
            </a>
          )}
        </>
      )}
      <p className="text-[12.5px] leading-relaxed text-mist-500" data-testid="share-link-text">
        {t('shares.link.text')}
      </p>
    </section>
  )
}

/** A calendar from iCloud, Google or CalDAV (AC-2, AC-10): "whole" or "busy only" per person, with the preview. What
 * is changed here (a right, a person taken away, a person added) waits for "Teilen"; "Abbrechen" drops it. */
function SourcePeople({ sharing, busy, act, search, onClose }: { sharing: Sharing; busy: boolean; act: Act; search: ReturnType<typeof useSearch>; onClose: () => void }) {
  const { t } = useTranslation()
  /** Per person already shared with: the new right, or "remove". */
  const [draft, setDraft] = useState<Record<number, ShareRight | 'remove'>>({})
  const [chosen, setChosen] = useState<SharePerson | null>(null)
  const [right, setRight] = useState<ShareRight>('busy')
  const id = sharing.calendar.id
  const changes = Object.entries(draft).filter(([personId, next]) => sharing.people.find((person) => person.id === Number(personId))?.right !== next)
  const pending = changes.length > 0 || chosen !== null
  const share = async () => {
    if (!pending) return
    await act(async () => {
      let answer = sharing
      for (const [personId, next] of changes) {
        answer = next === 'remove' ? await sharesApi.remove(id, Number(personId)) : await sharesApi.set(id, Number(personId), next)
      }
      if (chosen) answer = await sharesApi.set(id, chosen.id, right)
      return answer
    })
    onClose()
  }
  const rightOf = (person: SharePerson & { right: ShareRight }): ShareRight => {
    const next = draft[person.id]
    return next && next !== 'remove' ? next : person.right
  }
  const shown = sharing.people.filter((person) => draft[person.id] !== 'remove')
  const previewName = chosen?.name ?? shown[0]?.name ?? t('shares.source.other')
  const previewBusy = chosen ? right === 'busy' : shown[0] ? rightOf(shown[0]) === 'busy' : true
  return (
    <section className="flex flex-col gap-3">
      <p className="text-[13px] leading-relaxed text-mist-300" data-testid="share-source-hint">
        {t(`shares.source.noAccess.${sharing.calendar.kind}`)}
      </p>
      {shown.map((person) => (
        <PersonLine key={person.id} person={person}>
          <Seg value={rightOf(person)} label={t('shares.source.sees', { name: person.name })} disabled={busy} onChange={(next) => setDraft((before) => ({ ...before, [person.id]: next }))} />
          <RemoveButton person={person} busy={busy} onClick={() => setDraft((before) => ({ ...before, [person.id]: 'remove' }))} />
        </PersonLine>
      ))}
      {chosen ? (
        <PersonLine person={chosen}>
          <Seg value={right} label={t('shares.source.sees', { name: chosen.name })} disabled={busy} onChange={setRight} />
          <button type="button" className="nt-btn nt-btn-ghost h-[34px] w-[34px] border-transparent px-0" aria-label={t('shares.dialog.clear')} onClick={() => setChosen(null)}>
            <X aria-hidden className="h-4 w-4" />
          </button>
        </PersonLine>
      ) : (
        <SearchField search={search} onPick={(person) => (search.setQuery(''), setChosen(person))} onClose={onClose} />
      )}
      <div className="grid gap-3 sm:grid-cols-2" data-testid="share-preview">
        <Preview label={t('shares.source.youSee')} busy={false} color={sharing.calendar.color} />
        <Preview label={t('shares.source.theySee', { name: previewName })} busy={previewBusy} color={sharing.calendar.color} />
      </div>
      <p className="text-[12.5px] text-mist-500">{t('shares.source.busyText')}</p>
      <span className="flex justify-end gap-2">
        <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose} data-testid="share-cancel">
          {t('common.cancel')}
        </button>
        <button type="button" className="nt-btn nt-btn-accent" disabled={!pending || busy} onClick={() => void share()} data-testid="share-submit">
          {t('shares.source.share')}
        </button>
      </span>
    </section>
  )
}

/** "ganz zeigen | nur belegt". */
function Seg({ value, label, disabled, onChange }: { value: ShareRight; label: string; disabled: boolean; onChange: (right: ShareRight) => void }) {
  const { t } = useTranslation()
  return (
    <span role="group" aria-label={label} className="inline-flex overflow-hidden rounded-[9px] border border-ink-700 text-[13px]">
      {(['read', 'busy'] as const).map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={value === option}
          disabled={disabled}
          onClick={() => onChange(option)}
          className={'px-3 py-1.5 ' + (value === option ? 'nt-chosen' : 'text-mist-400 hover:text-mist-100')}
          data-testid={`share-seg-${option}`}
        >
          {t(`shares.right.${option === 'read' ? 'whole' : 'busy'}`)}
        </button>
      ))}
    </span>
  )
}

function Preview({ label, busy, color }: { label: string; busy: boolean; color: string }) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs text-mist-500">{label}</span>
      {busy ? (
        <div className="nt-busy flex h-[70px] flex-col rounded-[8px] px-2.5 py-2 text-[13px] text-mist-400">
          <strong>{t('shares.source.busyBlock')}</strong>
          <span>{t('shares.source.exampleTime')}</span>
        </div>
      ) : (
        <div className="flex h-[70px] flex-col rounded-[8px] px-2.5 py-2 text-[13px]" style={{ background: `color-mix(in srgb, ${colorVar(color)} 16%, transparent)` }}>
          <strong>{t('shares.source.exampleTitle')}</strong>
          <span>{t('shares.source.exampleTime')} · {t('shares.source.examplePlace')}</span>
        </div>
      )}
    </div>
  )
}
