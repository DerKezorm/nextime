/**
 * "What's new", as in nextasks: a banner under the head after an update, for every account, and the window with the
 * written text of the version. Each account puts it away for itself (`POST /api/me/whats-new/seen`); the server keeps
 * which version it read, so it does not come back on another device. A new account starts with the running version
 * read, so only an update shows the banner.
 *
 * The window is built like `Dialog` (scrim, Escape, focus back afterwards), with the lead under the title and the
 * button outside the scrolling part, so a long text does not move "Got it" out of reach.
 */
import { CornerDownRight, Sparkles, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { authApi } from '../api/client'
import { useWhatsNew, type WhatsNewEntry } from '../lib/whatsNew'
import { useAuth } from '../state/auth'

export function WhatsNewWindow({ version, entry, onClose }: { version: string; entry: WhatsNewEntry; onClose: () => void }) {
  const { t } = useTranslation()
  const titleId = useId()
  const done = useRef<HTMLButtonElement>(null)
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  }, [onClose])
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    done.current?.focus({ preventScroll: true })
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        close.current()
      }
    }
    window.addEventListener('keydown', key, true)
    return () => {
      window.removeEventListener('keydown', key, true)
      before?.focus?.()
    }
  }, [])
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-scrim p-4" onPointerDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="whats-new-window"
        className="flex max-h-[min(44rem,calc(100dvh-2rem))] w-full max-w-xl min-w-0 flex-col rounded-[16px] border border-ink-700 bg-ink-850 shadow-2xl shadow-black/50"
      >
        <div className="flex shrink-0 items-start gap-3 border-b border-ink-700/70 px-5 py-4">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-wash text-accent-400">
            <Sparkles className="h-4 w-4" strokeWidth={1.8} aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-semibold text-mist-100">
              {t('whatsNew.title', { version })}
            </h2>
            <p className="mt-1 text-sm text-mist-400">{entry.lead}</p>
          </div>
          <button type="button" onClick={onClose} aria-label={t('common.close')} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="nt-scroll min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {entry.sections.map((part) => (
            <section key={part.title}>
              <h3 className="font-semibold text-mist-100">{part.title}</h3>
              <p className="mt-0.5 flex items-start gap-1.5 text-xs text-accent-400">
                <CornerDownRight className="mt-px h-3.5 w-3.5 shrink-0" strokeWidth={1.8} aria-hidden />
                <span>
                  <span className="sr-only">{t('whatsNew.where')} </span>
                  {part.where}
                </span>
              </p>
              <p className="mt-1.5 text-sm leading-relaxed text-mist-300">{part.body}</p>
            </section>
          ))}
          {entry.small.length > 0 && (
            <section>
              <h3 className="text-xs font-medium tracking-wide text-mist-400 uppercase">{entry.smallTitle}</h3>
              <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-mist-300">
                {entry.small.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </section>
          )}
        </div>
        <div className="flex shrink-0 justify-end gap-2 px-5 pt-1 pb-5">
          <button ref={done} type="button" onClick={onClose} className="nt-btn nt-btn-accent">
            {t('whatsNew.done')}
          </button>
        </div>
      </div>
    </div>
  )
}

/** Under the head, as long as the running version has a text the account has not read or put away. */
export function WhatsNewBanner() {
  const { t, i18n } = useTranslation()
  const { me, refresh } = useAuth()
  const unread = !!me && me.whats_new_seen !== me.version
  const { entry } = useWhatsNew(me?.version, i18n.language, unread)
  const [open, setOpen] = useState(false)
  const [gone, setGone] = useState(false)
  if (!me || !unread || !entry || gone) return null

  function putAway() {
    setGone(true)
    void authApi.whatsNewSeen().then(() => refresh(), () => undefined)
  }

  return (
    <>
      <div className="flex shrink-0 items-center gap-3 border-b border-accent-500/30 bg-accent-wash px-4 py-2 text-sm text-mist-200" role="status" data-testid="whats-new-banner">
        <Sparkles className="h-4 w-4 shrink-0 text-accent-400" strokeWidth={1.8} aria-hidden />
        <span className="min-w-0 flex-1">{t('whatsNew.banner', { version: me.version })}</span>
        <button type="button" onClick={() => setOpen(true)} className="nt-btn nt-btn-accent h-8 px-3 text-xs">
          {t('whatsNew.show')}
        </button>
        <button type="button" onClick={putAway} aria-label={t('whatsNew.putAway')} title={t('whatsNew.putAway')} className="rounded p-0.5 text-mist-400 hover:text-mist-100">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {open && (
        <WhatsNewWindow
          version={me.version}
          entry={entry}
          onClose={() => {
            setOpen(false)
            putAway()
          }}
        />
      )}
    </>
  )
}
