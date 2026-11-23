/**
 * Invitations (TIME-18): the sign "1 Einladung" or "3 Einladungen" in the head of "Heute" (AC-1; a past one no longer
 * counts, EC-9) opens the one invitation in the side panel, or a short list of all of them (AC-6). In the side panel:
 * the invitation with its three answers (AC-4, AC-5, EC-1, EC-2, EC-5, EC-6), and the attendees of any event with them
 * (AC-3, EC-8). Names, titles and notes from outside are always text (EC-7).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '../../components/Dialog'
import { useDisplay } from '../../lib/useDisplay'
import type { Answer, Attendee, EventDetail } from '../calendars/api'
import { LinkedText } from '../calendars/links'
import { usePanel } from '../calendars/store'
import { clockText, dayName, wallOf } from '../calendars/time'
import type { Invitation } from './api'
import { useInvitations, useKindName } from './store'

export function InvitationSign() {
  const { t } = useTranslation()
  const list = useInvitations()
  const { open } = usePanel()
  const [listing, setListing] = useState(false)
  if (!list.length) return null
  return (
    <>
      <button
        type="button"
        className="nt-invitation-sign shrink-0"
        onClick={() => (list.length === 1 ? open({ kind: 'open', id: list[0].id, occurrence: list[0].occurrence }) : setListing(true))}
        data-testid="invitation-sign"
      >
        {t('invitations.count', { count: list.length })}
      </button>
      {listing && <InvitationList list={list} onClose={() => setListing(false)} />}
    </>
  )
}

function InvitationList({ list, onClose }: { list: Invitation[]; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const { zone, twelve } = useDisplay()
  const { open } = usePanel()
  const when = (entry: Invitation) => {
    if (entry.all_day) return dayName(entry.start.slice(0, 10), i18n.language)
    const start = wallOf(Date.parse(entry.start), zone)
    return `${dayName(start.date, i18n.language)} · ${clockText(start.time, i18n.language, twelve)}`
  }
  return (
    <Dialog title={t('invitations.listTitle')} onClose={onClose}>
      <ul className="space-y-1" data-testid="invitation-list">
        {list.map((entry) => (
          <li key={`${entry.id}-${entry.occurrence ?? ''}`}>
            <button
              type="button"
              className="flex w-full min-w-0 flex-col items-start gap-0.5 rounded-[9px] px-2 py-2 text-left text-sm hover:bg-ink-800"
              onClick={() => {
                onClose()
                open({ kind: 'open', id: entry.id, occurrence: entry.occurrence })
              }}
              data-testid="invitation-entry"
            >
              <span className="nt-wrap max-w-full font-medium text-mist-100">{entry.title}</span>
              <span className="max-w-full truncate text-xs text-mist-500">
                {when(entry)} · {t('invitations.from', { name: entry.by })}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Dialog>
  )
}

/** How many attendees show before "+n weitere" (EC-8). */
const FIRST = 8

/** "Teilnehmende · n" with initial, name and state; the inviting one "(lädt ein)", the member "Du" (AC-3, EC-8). */
export function Attendees({ detail }: { detail: EventDetail }) {
  const { t } = useTranslation()
  const [all, setAll] = useState(false)
  const people = detail.attendees ?? []
  if (!people.length) return null
  const shown = all ? people : people.slice(0, FIRST)
  const count = detail.attendees_count ?? people.length
  return (
    <section className="space-y-2 border-t border-ink-700 pt-3" aria-labelledby="attendees-title" data-testid="attendees">
      <h3 id="attendees-title" className="text-xs font-semibold tracking-wider text-mist-500 uppercase">
        {t('invitations.attendees', { count })}
      </h3>
      <ul className="space-y-1.5">
        {shown.map((person, index) => (
          <li key={`${person.address}-${index}`} className="flex min-w-0 items-center gap-2.5 text-sm" data-testid="attendee">
            <span aria-hidden className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-ink-700 text-[11px] font-semibold text-mist-200">
              {initial(person)}
            </span>
            <span className="min-w-0 flex-1 truncate text-mist-100" title={nameOf(person, t)} data-testid="attendee-name">
              {nameOf(person, t)}
            </span>
            <span className={'shrink-0 text-xs ' + stateLook(person.state)} data-testid="attendee-state">
              {t(`invitations.states.${person.state}`)}
            </span>
          </li>
        ))}
      </ul>
      {!all && people.length > FIRST && (
        <button type="button" className="nt-btn nt-btn-quiet text-sm" onClick={() => setAll(true)} data-testid="attendees-more">
          {t('invitations.more', { count: people.length - FIRST })}
        </button>
      )}
    </section>
  )
}

function initial(person: Attendee): string {
  const name = (person.name || person.address).trim()
  return name ? Array.from(name)[0].toUpperCase() : '?'
}

function nameOf(person: Attendee, t: (key: string, values?: Record<string, unknown>) => string): string {
  const name = person.self ? t('invitations.you') : person.name || person.address
  return person.organizer ? t('invitations.organizer', { name }) : name
}

function stateLook(state: Attendee['state']): string {
  return state === 'accepted' ? 'text-ok-500' : state === 'tentative' ? 'text-warn-500' : state === 'declined' ? 'text-bad-500' : 'text-mist-500'
}

/**
 * The side panel of an invitation (AC-4): calendar and source ("Arbeit · iCloud"), the dashed mark "Einladung offen",
 * title, day, time, place, "Eingeladen von <Name>", the three answers with the chosen one pressed (AC-5), where the
 * answer goes, the notes and the attendees. Where no answer can go out, the buttons are missing with the reason (EC-2).
 */
export function InvitationView({ detail, calendarName, busy, note, onAnswer }: {
  detail: EventDetail
  calendarName: string
  busy: boolean
  /** A quiet sentence above, as "the invitation changed meanwhile, this is how it stands now" (EC-1). */
  note: string | null
  onAnswer: (answer: Answer) => void
}) {
  const { t, i18n } = useTranslation()
  const { twelve } = useDisplay()
  const kindName = useKindName()
  const invitation = detail.invitation!
  const kind = invitation.source || detail.source || ''
  const day = dayName(detail.start.slice(0, 10), i18n.language)
  const when = detail.all_day ? day : `${day} · ${clockText(detail.start.slice(11, 16), i18n.language, twelve)}–${clockText(detail.end.slice(11, 16), i18n.language, twelve)}`
  const answers: { value: Answer; label: string }[] = [
    { value: 'accepted', label: t('invitations.accept') },
    { value: 'tentative', label: t('invitations.maybe') },
    { value: 'declined', label: t('invitations.decline') },
  ]
  return (
    <div className="space-y-3" data-testid="invitation">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 truncate text-sm text-mist-400" data-testid="invitation-source">
          {kind ? `${calendarName} · ${kindName(kind)}` : calendarName}
        </span>
        {invitation.state === 'open' && !invitation.cancelled && (
          <span className="nt-mark nt-mark-open" data-testid="invitation-open">
            {t('invitations.openMark')}
          </span>
        )}
      </div>
      {note && (
        <p role="status" className="rounded-[9px] border border-ink-700 bg-[var(--color-accent-wash)] px-3 py-2 text-sm text-mist-200" data-testid="invitation-note">
          {note}
        </p>
      )}
      <strong className="nt-wrap block text-lg text-mist-100" data-testid="invitation-title-full">
        {detail.title}
      </strong>
      <span className="block text-sm text-mist-500" data-testid="invitation-when">
        {when}
        {detail.location ? ` · ${detail.location}` : ''}
        {detail.waiting && (
          <>
            {' '}
            <span className="nt-waiting" data-testid="waiting">
              {t('views.waiting')}
            </span>
          </>
        )}
      </span>
      <span className="block text-sm text-mist-200" data-testid="invitation-by">
        {t('invitations.invitedBy', { name: invitation.by })}
      </span>
      {invitation.cancelled ? (
        <p role="status" className="rounded-[9px] border border-warn-500/30 bg-warn-wash px-3 py-2 text-sm text-warn-500" data-testid="invitation-cancelled">
          {t('invitations.cancelled', { name: invitation.by })}
        </p>
      ) : invitation.can_answer ? (
        <>
          <div className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-3" data-testid="answers">
            {answers.map((answer) => {
              const chosen = invitation.state === answer.value
              return (
                <button
                  key={answer.value}
                  type="button"
                  aria-pressed={chosen}
                  disabled={busy}
                  className={'nt-btn ' + (chosen ? 'nt-btn-accent' : 'nt-btn-ghost')}
                  onClick={() => onAnswer(answer.value)}
                  data-testid={`answer-${answer.value}`}
                >
                  {answer.label}
                </button>
              )
            })}
          </div>
          <span className="block text-xs text-mist-500" data-testid="answer-goes">
            {t('invitations.goesTo', { source: kindName(kind), name: invitation.by })}
          </span>
        </>
      ) : (
        <p role="status" className="rounded-[9px] border border-ink-700 bg-[var(--color-accent-wash)] px-3 py-2 text-sm text-mist-200" data-testid="no-answers">
          {t(`invitations.whyNot.${invitation.why_not || 'read_only_source'}`)}
        </p>
      )}
      {detail.notes && (
        <p className="border-t border-ink-700 pt-3 text-sm break-words whitespace-pre-wrap text-mist-300" data-testid="invitation-notes">
          <LinkedText text={detail.notes} />
        </p>
      )}
      <Attendees detail={detail} />
    </div>
  )
}
