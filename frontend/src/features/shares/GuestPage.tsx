/**
 * The guest page of a read link (TIME-19 AC-13, T19-Gast), without an account: always light, whatever the device is
 * set to; the calendar's name and color, "Geteilt von <Name> · nur lesen", "In meinem Kalender abonnieren" (Apple
 * Calendar on an iPhone or Mac opens the subscription, `webcal://`), "Adresse kopieren", the sentence about the
 * subscription, the coming events by day (date, time or "ganzt.", title, place) with "Weitere anzeigen", and "Erstellt
 * mit nextime". Times in the guest's own zone and language (EC-19, EC-23); every text as plain text (EC-10). A link that
 * no longer holds says only that (EC-1), without the calendar's name.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { Logo } from '../../components/Logo'
import { copyText } from '../../lib/copy'
import { colorVar } from '../calendars/Swatch'
import { guestApi, type GuestEvent, type GuestPage as Page } from './api'

/** A page that looks the same on every device: light, as the attrappe drew it. */
function useLight(): void {
  useEffect(() => {
    const root = document.documentElement
    const before = root.getAttribute('data-theme')
    root.setAttribute('data-theme', 'light')
    return () => {
      if (before === null) root.removeAttribute('data-theme')
      else root.setAttribute('data-theme', before)
    }
  }, [])
}

/** The local day of an event: a whole day is its date; a time, the date on the guest's clock. */
function dayOf(event: GuestEvent): string {
  if (event.all_day) return event.start
  const date = new Date(event.start)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export function GuestPage() {
  const { token = '' } = useParams()
  const { t, i18n } = useTranslation()
  const [page, setPage] = useState<Page | null>(null)
  const [events, setEvents] = useState<GuestEvent[]>([])
  const [gone, setGone] = useState(false)
  const [copied, setCopied] = useState(false)
  const [loading, setLoading] = useState(false)
  useLight()

  useEffect(() => {
    guestApi.page(token).then(
      (answer) => {
        setPage(answer)
        setEvents(answer.events)
      },
      () => setGone(true),
    )
  }, [token])

  const more = async () => {
    if (!page || loading) return
    setLoading(true)
    try {
      const next = await guestApi.page(token, page.next)
      setPage(next)
      setEvents((before) => [...before, ...next.events])
    } catch {
      setGone(true)
    } finally {
      setLoading(false)
    }
  }

  const language = i18n.language
  const dayName = (value: string) =>
    new Date(`${value}T12:00:00`).toLocaleDateString(language.startsWith('de') ? 'de-DE' : language, { weekday: 'short', day: 'numeric', month: 'short' })
  const time = (value: string) => new Date(value).toLocaleTimeString(language.startsWith('de') ? 'de-DE' : language, { hour: 'numeric', minute: '2-digit' })

  return (
    <div className="min-h-dvh bg-ink-950 text-mist-100" data-testid="guest-page">
      <div className="mx-auto flex w-full max-w-[720px] flex-col gap-5 px-4 py-10">
        {gone && (
          <div className="nt-empty" data-testid="guest-gone">
            <strong className="text-mist-100">{t('guest.gone')}</strong>
          </div>
        )}
        {page && !gone && (
          <>
            <header className="flex flex-wrap items-center gap-3">
              <span aria-hidden className="h-3.5 w-3.5 shrink-0 rounded-[4px]" style={{ background: colorVar(page.calendar.color) }} />
              <h1 className="min-w-0 text-[28px] leading-tight font-extrabold break-words" data-testid="guest-name">
                {page.calendar.name}
              </h1>
              <span className="text-[13.5px] text-mist-500" data-testid="guest-owner">
                {t('guest.sharedBy', { name: page.owner })}
              </span>
            </header>
            <div className="flex flex-wrap gap-2.5">
              <a className="nt-btn nt-btn-accent" href={page.feed.replace(/^https?:/, 'webcal:')} data-testid="guest-subscribe">
                {t('guest.subscribe')}
              </a>
              <button type="button" className="nt-btn nt-btn-ghost" onClick={() => void copyText(page.feed).then(setCopied)} data-testid="guest-copy">
                {copied ? t('guest.copied') : t('guest.copy')}
              </button>
            </div>
            <p className="text-[13px] text-mist-500">{t('guest.stays', { name: page.owner })}</p>
            {events.length === 0 ? (
              <p className="nt-empty" data-testid="guest-none">
                {t('guest.none')}
              </p>
            ) : (
              <ul className="flex flex-col" data-testid="guest-events">
                {events.map((event, index) => {
                  const date = dayOf(event)
                  const shown = index === 0 || dayOf(events[index - 1]) !== date ? dayName(date) : ''
                  return (
                    <li key={index} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-ink-800 py-2.5" data-testid="guest-event">
                      <span className="w-32 shrink-0 text-[13.5px] text-mist-500">{shown}</span>
                      <span className="w-16 shrink-0 text-[13.5px] text-mist-500">{event.all_day ? t('guest.allDay') : time(event.start)}</span>
                      <span className="min-w-0 flex-1 text-[14.5px] font-medium break-words">{event.title}</span>
                      {event.location && <span className="min-w-0 basis-full pl-0 text-[13px] text-mist-500 break-words sm:basis-auto">{event.location}</span>}
                    </li>
                  )
                })}
              </ul>
            )}
            {page.more && (
              <button type="button" className="nt-btn nt-btn-ghost w-max" disabled={loading} onClick={() => void more()} data-testid="guest-more">
                {t('guest.more')}
              </button>
            )}
          </>
        )}
        <footer className="mt-4 flex items-center justify-center gap-2 text-xs text-mist-500">
          <Logo size={16} />
          {t('guest.madeWith')}
        </footer>
      </div>
    </div>
  )
}
