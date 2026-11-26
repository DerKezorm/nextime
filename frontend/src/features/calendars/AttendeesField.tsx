/**
 * The field "Teilnehmer" of an event in an iCloud or Google calendar (TIME-18 AC-9, plan "Apps verbinden" V30): the
 * people to invite as chips, a free mail address with Enter, and suggestions from nexfolk's people over a connection
 * (`GET /api/people`, only name and mail addresses). nextime writes them into the event at the source, which sends the
 * invitations as usual. Not shown for nextime's own calendars.
 */
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'

export type Invitee = { email: string; name: string }
type Person = { id: string; name: string; emails: string[] }

/** The sources that take attendees and send the invitations themselves. */
// eslint-disable-next-line react-refresh/only-export-components
export const INVITING_SOURCES = ['icloud', 'google']

const MAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function AttendeesField({ value, onChange, disabled = false }: { value: Invitee[]; onChange: (next: Invitee[]) => void; disabled?: boolean }) {
  const { t } = useTranslation()
  const id = useId()
  const [typed, setTyped] = useState('')
  const [found, setFound] = useState<Person[]>([])
  const [problem, setProblem] = useState('')
  const words = typed.trim()
  useEffect(() => {
    if (words.length < 2) return
    let live = true
    api<{ people?: Person[] }>(`/api/people?q=${encodeURIComponent(words)}`).then(
      (answer) => live && setFound(answer && !Array.isArray(answer) && answer.people ? answer.people : []),
      () => live && setFound([]),
    )
    return () => {
      live = false
    }
  }, [words])
  const add = (email: string, name: string) => {
    const address = email.trim()
    if (!MAIL.test(address)) return setProblem(t('panel.attendeeInvalid'))
    setProblem('')
    if (!value.some((one) => one.email.toLowerCase() === address.toLowerCase())) onChange([...value, { email: address, name }])
    setTyped('')
    setFound([])
  }
  const suggestions = words.length < 2 ? [] : found.flatMap((person) => person.emails.map((email) => ({ email, name: person.name }))).slice(0, 8)
  return (
    <div className="text-sm" data-testid="field-attendees">
      <label htmlFor={id} className="font-medium text-mist-100">
        {t('panel.attendees')}
      </label>
      {value.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="attendee-chips">
          {value.map((one) => (
            <li key={one.email} className="flex items-center gap-1 rounded-full bg-ink-800 px-2.5 py-1 text-xs text-mist-100" data-testid="attendee-chip">
              <span title={one.email}>{one.name || one.email}</span>
              <button type="button" className="text-mist-500 hover:text-mist-100" disabled={disabled} aria-label={t('panel.attendeeRemove', { name: one.name || one.email })} onClick={() => onChange(value.filter((other) => other !== one))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        id={id}
        className={'nt-field mt-1 ' + (problem ? 'border-bad-500' : '')}
        value={typed}
        disabled={disabled}
        placeholder={t('panel.attendeeAdd')}
        onChange={(event) => setTyped(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && words) {
            event.preventDefault()
            add(words, '')
          }
        }}
        data-testid="attendee-input"
      />
      {suggestions.length > 0 && (
        <ul className="mt-1 rounded-[10px] border border-ink-700 bg-ink-850 py-1" role="listbox" data-testid="attendee-suggestions">
          {suggestions.map((one) => (
            <li key={`${one.name}-${one.email}`}>
              <button type="button" className="flex w-full items-baseline justify-between gap-3 px-3 py-1.5 text-left hover:bg-ink-800" onClick={() => add(one.email, one.name)} data-testid="attendee-suggestion">
                <span className="truncate text-mist-100">{one.name || one.email}</span>
                <span className="shrink-0 text-xs text-mist-500">{one.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {problem ? (
        <p role="alert" className="mt-1 text-xs text-bad-500" data-testid="attendee-problem">
          {problem}
        </p>
      ) : (
        <p className="mt-1 text-xs text-mist-500">{t('panel.attendeesHint')}</p>
      )}
    </div>
  )
}
