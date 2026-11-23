/**
 * "<Kalender> · Einstellungen" (TIME-21, T21-Mitteilungen; the entry "Einstellungen und Mitteilungen" of every calendar):
 * "Bearbeiten …" and "Teilen …" for the owner, the switch "Mitteilung, wenn andere hier etwas ändern" with its sentence
 * (AC-1), "Geräte mit Mitteilungen" with this browser's state and "Mitteilungen erlauben" (AC-5, EC-8, EC-9), what
 * keeps notifications from arriving (no HTTPS, no internet, EC-14) and "So sieht die Mitteilung aus" with a bundle and
 * one of a "busy only" calendar (AC-6). The switch, the devices and the preview are laid out as nexnotes' notification
 * panel (`notify/NotifyPanel.tsx` there).
 */
import { Pencil, Share2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'

import { CheckLine } from '../../components/CheckLine'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { LogoMark } from '../../components/Logo'
import { NotFound } from '../../components/NotFound'
import { problemText } from '../../lib/errors'
import { CalendarDialog } from '../calendars/CalendarDialogs'
import { calendarsApi } from '../calendars/api'
import type { NewDefaults } from '../calendars/defaults'
import { NewEventDefaults } from '../calendars/NewEventDefaults'
import { useCalendars } from '../calendars/store'
import { Swatch } from '../calendars/Swatch'
import { shareKind } from '../shares/api'
import { ShareDialog } from '../shares/ShareDialog'
import { notifyApi, type NotifyBrowser, type NotifySettings, type NotifyText } from './api'
import { allowBrowser, browserState, stopBrowser, type BrowserState } from './browser'

export function CalendarSettingsPage() {
  const { id = '' } = useParams()
  const calendarId = Number(id)
  const { t } = useTranslation()
  const { list, take } = useCalendars()
  const [settings, setSettings] = useState<NotifySettings | null>(null)
  const [browser, setBrowser] = useState<BrowserState | null>(null)
  const [problem, setProblem] = useState('')
  const [missing, setMissing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [dialog, setDialog] = useState<'edit' | 'share' | null>(null)

  const load = useCallback(() => {
    notifyApi.get(calendarId).then(
      (found) => {
        setSettings(found)
        setProblem('')
      },
      (error) => {
        if (error?.status === 404) setMissing(true)
        else setProblem(problemText(error))
      },
    )
  }, [calendarId])
  useEffect(load, [load])
  useEffect(() => {
    void browserState().then(setBrowser)
  }, [])

  // One step at a time: a second click while the first is on its way does nothing.
  const running = useRef(false)
  const step = async (work: () => Promise<void>) => {
    if (running.current) return
    running.current = true
    setBusy(true)
    setProblem('')
    try {
      await work()
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      running.current = false
      setBusy(false)
    }
  }
  const saveDefaults = (defaults: NewDefaults) =>
    step(async () => {
      take(await calendarsApi.change(calendarId, { defaults }))
    })
  const toggle = () =>
    step(async () => {
      if (settings) setSettings(await notifyApi.set(calendarId, !settings.on))
    })
  const allow = () => step(async () => (setBrowser(await allowBrowser()), load()))
  const stop = () => step(async () => (setBrowser(await stopBrowser()), load()))

  if (missing)
    return (
      // A calendar that is not there (any more), or not for this account: said as nexnotes says it, with the way back.
      <div className="px-4" data-testid="calendar-settings-missing">
        <NotFound text={t('notify.missing')} />
      </div>
    )
  if (!settings)
    return problem ? (
      <p role="alert" className="p-6 text-sm text-bad-500">
        {problem}
      </p>
    ) : null

  const entry = list?.calendars.find((calendar) => calendar.id === calendarId)
  const kind = entry ? shareKind(entry.right, entry.origin) : null
  const others = settings.devices.browsers.filter((each) => !each.this)
  const nowhere = settings.on && settings.devices.browsers.length === 0
  return (
    <div className="mx-auto grid max-w-5xl gap-8 p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]" data-testid="calendar-settings">
      <section className="flex min-w-0 flex-col gap-4">
        <h1 className="flex min-w-0 items-center gap-2.5 text-2xl font-bold">
          <Swatch color={settings.calendar.color} size={14} />
          <span className="min-w-0 truncate" title={settings.calendar.name}>
            {t('notify.heading', { name: settings.calendar.name })}
          </span>
        </h1>
        {settings.calendar.owner && <p className="-mt-2 text-sm text-mist-500">{t('notify.sharedBy', { name: settings.calendar.owner })}</p>}
        {(entry?.right === 'owner' && !entry.origin) || kind ? (
          <div className="flex flex-wrap gap-2">
            {entry?.right === 'owner' && !entry.origin && (
              <button type="button" className="nt-btn nt-btn-ghost h-9 text-sm" onClick={() => setDialog('edit')} data-testid="calendar-settings-edit">
                <Pencil aria-hidden className="h-4 w-4" />
                {t('calendars.edit')}
              </button>
            )}
            {kind && (
              <button type="button" className="nt-btn nt-btn-ghost h-9 text-sm" onClick={() => setDialog('share')} data-testid="calendar-settings-share">
                <Share2 aria-hidden className="h-4 w-4" />
                {t('shares.menu')}
              </button>
            )}
          </div>
        ) : null}

        {entry && entry.writable && !(entry.right === 'owner' && !entry.origin) && (
          // A calendar shared with the person, or one of a source: its defaults for new events here, the person's own
          // (TIME-4); an own calendar has them in its dialog. Not for one only to be read: no event comes about there.
          <div className="rounded-[12px] border border-ink-700 bg-ink-850 p-4" data-testid="calendar-settings-defaults">
            <NewEventDefaults value={entry.defaults ?? {}} fallbackReminders={() => []} onChange={(defaults) => void saveDefaults(defaults)} />
            <p className="mt-2 text-xs text-mist-500">{t('calendars.defaults.page')}</p>
          </div>
        )}

        <div className="flex flex-col gap-2 rounded-[12px] border border-ink-700 bg-ink-850 p-4" data-testid="notify-card">
          {settings.shared ? (
            <>
              <CheckLine label={t('notify.switch')} hint={t('notify.bundled')} checked={settings.on} disabled={busy} onChange={() => void toggle()} testId="notify-switch" className="text-[15px] font-semibold" />
            </>
          ) : (
            <span className="text-[13px] leading-relaxed text-mist-400" data-testid="notify-not-shared">
              {t('notify.notShared')}
            </span>
          )}
        </div>

        <div className="flex flex-col gap-2.5 rounded-[12px] border border-ink-700 bg-ink-850 p-4" data-testid="notify-devices">
          <strong className="text-sm">{t('notify.devices')}</strong>
          {others.map((each) => (
            <span key={each.id} className="flex items-center gap-2.5 text-[14px]">
              <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-ok-500" />
              <span className="min-w-0 flex-1 truncate">{browserName(each, t)}</span>
              <span className="text-[12.5px] text-mist-500">{t('notify.allowed')}</span>
            </span>
          ))}
          <span className="flex items-center gap-2.5 text-[14px]" data-testid="notify-this-browser" data-state={browser ?? ''}>
            <span aria-hidden className={'h-2 w-2 shrink-0 rounded-full ' + (browser === 'on' ? 'bg-ok-500' : 'bg-warn-500')} />
            <span className="min-w-0 flex-1">{t('notify.thisBrowser')}</span>
            {browser === 'on' && (
              <span className="flex items-center gap-2">
                <span className="text-[12.5px] text-mist-500">{t('notify.allowed')}</span>
                <button type="button" className="nt-btn nt-btn-quiet h-8 px-2 text-[12.5px]" disabled={busy} onClick={() => void stop()} data-testid="notify-stop">
                  {t('notify.stop')}
                </button>
              </span>
            )}
            {browser === 'off' && (
              <button type="button" className="nt-btn nt-btn-accent h-8 px-2.5 text-[12.5px]" disabled={busy} onClick={() => void allow()} data-testid="notify-allow">
                {t('notify.allow')}
              </button>
            )}
            {browser === 'denied' && <span className="text-[12.5px] text-warn-500">{t('notify.blocked')}</span>}
            {browser === 'unsupported' && <span className="text-[12.5px] text-mist-500">{t('notify.unsupported')}</span>}
          </span>
          {browser === 'denied' && (
            <p className="text-[12.5px] leading-relaxed text-mist-300" data-testid="notify-denied-help">
              {t('notify.deniedHelp')}
            </p>
          )}
          {browser === 'unsupported' && <p className="text-[12.5px] leading-relaxed text-mist-300">{t('notify.unsupportedHelp')}</p>}
        </div>

        {(nowhere || !settings.delivery.https || !settings.delivery.internet) && (
          <div className="flex flex-col gap-1.5 rounded-[12px] border border-warn-500/30 bg-[var(--color-warn-wash)] px-4 py-3 text-sm text-warn-500" role="status" data-testid="notify-warning">
            {nowhere && <span data-testid="notify-nowhere">{t('notify.nowhere')}</span>}
            {!settings.delivery.https && <span data-testid="notify-no-https">{t('notify.noHttps')}</span>}
            {!settings.delivery.internet && <span data-testid="notify-no-internet">{t('notify.noInternet')}</span>}
          </div>
        )}
        {problem && (
          <p role="alert" className="text-sm text-bad-500">
            {problem}
          </p>
        )}
      </section>

      <section className="flex flex-col gap-3 lg:pt-12" data-testid="notify-preview">
        <span className="text-xs font-semibold tracking-wider text-mist-500 uppercase">{t('notify.preview')}</span>
        <PreviewCard text={settings.preview.bundle} logo />
        <PreviewCard text={settings.preview.busy} />
      </section>

      {dialog === 'edit' && entry && <CalendarDialog calendar={entry} onDone={(answer) => (take(answer), setDialog(null), load())} onClose={() => setDialog(null)} />}
      {dialog === 'share' && <ShareDialog calendarId={calendarId} name={settings.calendar.name} color={settings.calendar.color} onClose={() => (setDialog(null), load())} />}
    </div>
  )
}

function PreviewCard({ text, logo = false }: { text: NotifyText; logo?: boolean }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-ink-700 bg-ink-800 px-3 py-2.5" data-testid="notify-preview-card">
      {logo && <LogoMark size={30} />}
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="truncate text-[13.5px] font-semibold">{text.title}</span>
        <span className="text-[13px] leading-snug text-mist-300">{text.text}</span>
      </span>
    </div>
  )
}

function browserName(each: NotifyBrowser, t: (key: string, values?: Record<string, string>) => string): string {
  if (each.installed) return t('notify.webApp', { system: each.system || t('notify.device') })
  if (each.browser && each.system) return t('notify.browserOn', { browser: each.browser, system: each.system })
  return each.browser || each.system || t('notify.otherBrowser')
}
