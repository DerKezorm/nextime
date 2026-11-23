/**
 * "Add calendar" from start to end, the same from the sidebar and from "Calendars and accounts" (TIME-15 AC-7): the
 * ways, then the dialog of the way chosen, after connecting an account the choice of its calendars (AC-9).
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Dialog } from '../../components/Dialog'
import { problemText } from '../../lib/errors'
import { AddCalendarDialog, CalendarDialog, type Way } from '../calendars/CalendarDialogs'
import { useCalendars } from '../calendars/store'
import { sourcesApi, type Choices, type Sources } from './api'
import { ChooseDialog, ConnectDialog, GoogleDialog, GoogleReadDialog, HolidaysDialog, SubscribeDialog } from './Dialogs'
import { showNotice } from '../../lib/notice'
import { useSources } from './store'

type Step =
  | { kind: 'ways' }
  | { kind: 'way'; way: Way }
  | { kind: 'choose'; choices: Choices; connected: boolean; note?: string | null }
  | { kind: 'existing'; sourceId: number }

export function AddCalendar({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const calendars = useCalendars()
  const sources = useSources()
  const [step, setStep] = useState<Step>({ kind: 'ways' })
  const navigate = useNavigate()
  const count = calendars.list?.count ?? 0
  const limit = calendars.list?.smooth_up_to ?? 20
  const done = (answer: Sources, said?: string) => {
    sources.take(answer)
    if (said) showNotice({ text: said })
    onClose()
  }

  if (step.kind === 'ways')
    return (
      <AddCalendarDialog
        count={count}
        limit={limit}
        google={sources.data?.google ?? null}
        onWay={(way) => {
          // "Datei laden" is the loading of "Mitnehmen und umziehen" (TIME-24 AC-5).
          if (way === 'file') {
            onClose()
            navigate('/settings/move#move-load')
            return
          }
          setStep({ kind: 'way', way })
        }}
        onClose={onClose}
      />
    )
  if (step.kind === 'choose')
    return <ChooseDialog choices={step.choices} connected={step.connected} note={step.note} onDone={(answer) => done(answer)} onClose={onClose} />
  if (step.kind === 'existing') return <ChooseSource sourceId={step.sourceId} note={t('sources.exists')} onClose={onClose} />
  switch (step.way) {
    case 'own':
      return <CalendarDialog onDone={(answer) => (calendars.take(answer), onClose())} onClose={onClose} />
    case 'icloud':
    case 'caldav':
      return (
        <ConnectDialog
          kind={step.way}
          onConnected={(choices) => setStep({ kind: 'choose', choices, connected: true })}
          onExists={(sourceId) => setStep({ kind: 'existing', sourceId })}
          onClose={onClose}
        />
      )
    case 'google':
      return <GoogleDialog onClose={onClose} />
    case 'googleRead':
      return <GoogleReadDialog onDone={(answer) => done(answer, t('sources.added'))} onClose={onClose} />
    case 'subscription':
      return <SubscribeDialog onDone={(answer) => done(answer, t('sources.added'))} onClose={onClose} />
    case 'holidays':
      return <HolidaysDialog rows={sources.data?.sources ?? []} onDone={(answer) => done(answer)} onClose={onClose} />
    default:
      return null
  }
}

/** The choice of calendars of a connected account: "Kalender wählen" (AC-9), after Google came back (TIME-16 AC-2), or
 * the existing connection when the same account was connected again (EC-14). */
export function ChooseSource({ sourceId, connected = false, note, onClose }: { sourceId: number; connected?: boolean; note?: string | null; onClose: () => void }) {
  const { t } = useTranslation()
  const sources = useSources()
  const [choices, setChoices] = useState<Choices | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    sourcesApi
      .choices(sourceId)
      .then((answer) => live && setChoices(answer))
      .catch((error) => live && setProblem(problemText(error)))
    return () => {
      live = false
    }
  }, [sourceId])
  if (problem)
    return (
      <Dialog title={t('sources.chooseAny')} onClose={onClose}>
        <p role="alert" className="text-sm text-bad-500" data-testid="choose-problem">
          {problem}
        </p>
      </Dialog>
    )
  if (!choices) return null
  return (
    <ChooseDialog
      choices={choices}
      connected={connected}
      note={note}
      onDone={(answer) => (sources.take(answer), onClose())}
      onClose={onClose}
    />
  )
}
