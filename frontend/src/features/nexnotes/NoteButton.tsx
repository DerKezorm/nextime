/**
 * "Notiz in nexnotes" in an event's panel (plan "Apps verbinden" V19, pair nextime → nexnotes, draft of the smallest
 * form): over a connection made by a click, one click makes a note in nexnotes' inbox with the event's title, time,
 * place and notes; the same event again finds the note made before. Without a connection: nothing (V20).
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'

let connected: Promise<boolean> | null = null

/** Asked once per page: whether nextime is connected with nexnotes. */
function isConnected(): Promise<boolean> {
  connected ??= api<{ connected?: boolean }>('/api/nexnotes').then(
    (answer) => Boolean(answer && !Array.isArray(answer) && answer.connected),
    () => false,
  )
  return connected
}

// eslint-disable-next-line react-refresh/only-export-components
export function forgetNexnotes(): void {
  connected = null
}

export function NoteButton({ eventId, occurrence }: { eventId: number; occurrence?: string | null }) {
  const { t } = useTranslation()
  const [shown, setShown] = useState(false)
  const [state, setState] = useState<'idle' | 'busy' | 'made' | 'found' | 'failed'>('idle')
  useEffect(() => {
    let live = true
    void isConnected().then((yes) => live && setShown(yes))
    return () => {
      live = false
    }
  }, [])
  if (!shown) return null
  const send = async () => {
    setState('busy')
    try {
      const query = occurrence ? `?occurrence=${encodeURIComponent(occurrence)}` : ''
      const answer = await api<{ made: boolean }>(`/api/events/${eventId}/nexnotes${query}`, { method: 'POST' })
      setState(answer.made ? 'made' : 'found')
    } catch {
      setState('failed')
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="nexnotes-note">
      <button type="button" className="nt-btn nt-btn-ghost" disabled={state === 'busy'} onClick={() => void send()} data-testid="nexnotes-note-button">
        {t('nexnotes.note')}
      </button>
      {(state === 'made' || state === 'found' || state === 'failed') && (
        <span role="status" className={'text-xs ' + (state === 'failed' ? 'text-bad-500' : 'text-mist-500')} data-testid="nexnotes-note-state">
          {t(`nexnotes.${state}`)}
        </span>
      )}
    </div>
  )
}
