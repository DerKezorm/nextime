/**
 * Settings › My account › "Erinnerungen" (TIME-7 AC-6, AC-7, AC-8, T7-Erinnerung), in the cards and checkbox rows of
 * the settings as nexnotes lays them out: "Auf diesem Gerät erinnern" (this browser only) and "Nicht doppelt, wenn
 * Apple Kalender schon erinnert" with its sentence; "Vorgabe je Kalender" with color, name and choice per calendar
 * ("wie in iCloud" for connected ones); the devices that remind; what keeps reminders from arriving (a browser that
 * blocks them, Safari outside the installed web app, no HTTPS, no internet; EC-2, EC-4, EC-5, EC-15); and "So sieht
 * die Erinnerung aus" with "In 5 Min erneut" and "Öffnen". The browser's permission and sign-up are the ones of TIME-21
 * (`features/notify/browser.ts`).
 */
import { Bell, CalendarDays, Smartphone } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { LogoMark } from '../../components/Logo'
import { problemText } from '../../lib/errors'
import { Card, Toggle } from '../../pages/settings/ui'
import { reminderLabel } from '../calendars/labels'
import { Swatch } from '../calendars/Swatch'
import { allowBrowser, browserState, keepRemindHere, type BrowserState } from '../notify/browser'
import { remindersApi, type ReminderBrowser, type ReminderCalendar, type ReminderSettings } from './api'

export function RemindersArea() {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<ReminderSettings | null>(null)
  const [browser, setBrowser] = useState<BrowserState | null>(null)
  const [problem, setProblem] = useState('')
  const running = useRef(false)

  const load = useCallback(() => {
    remindersApi.get().then(
      (found) => (setSettings(found), setProblem('')),
      (error) => setProblem(problemText(error)),
    )
  }, [])
  useEffect(load, [load])
  useEffect(() => {
    void browserState().then(setBrowser)
  }, [])

  /** One step at a time: a second click while the first is on its way does nothing. */
  const step = async (work: () => Promise<ReminderSettings | void>) => {
    if (running.current) return
    running.current = true
    setProblem('')
    try {
      const answer = await work()
      if (answer) setSettings(answer)
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      running.current = false
    }
  }

  if (!settings)
    return problem ? (
      <p role="alert" className="text-sm text-bad-500">
        {problem}
      </p>
    ) : null

  const here = settings.this_browser
  const reminding = settings.browsers.filter((each) => each.reminders && !each.quiet)
  return (
    <div className="flex min-w-0 flex-col gap-6" data-testid="reminders-area">
        <Card icon={Bell} title={t('settings.areas.reminders.title')}>
          <Toggle
            label={t('reminders.area.device')}
            hint={here.subscribed ? t('reminders.area.deviceHint') : t('reminders.area.deviceFirst')}
            checked={here.subscribed && here.reminders}
            disabled={!here.subscribed}
            onChange={(on) => void step(async () => (keepRemindHere(on), remindersApi.device(on)))}
          />
          <Toggle label={t('reminders.area.notDouble')} hint={t('reminders.area.notDoubleHint')} checked={settings.not_double} onChange={(on) => void step(() => remindersApi.notDouble(on))} />
          <BrowserHint state={browser} onAllow={() => void step(async () => (setBrowser(await allowBrowser()), remindersApi.get()))} />
          {(reminding.length === 0 || !settings.delivery.https || !settings.delivery.internet) && (
            <div className="flex flex-col gap-1.5 rounded-[12px] border border-warn-500/30 bg-[var(--color-warn-wash)] px-4 py-3 text-sm text-warn-500" role="status" data-testid="reminders-warning">
              {reminding.length === 0 && <span data-testid="reminders-no-device">{t('reminders.area.noDevice')}</span>}
              {!settings.delivery.https && <span data-testid="reminders-no-https">{t('notify.noHttps')}</span>}
              {!settings.delivery.internet && <span data-testid="reminders-no-internet">{t('notify.noInternet')}</span>}
            </div>
          )}
          <div className="flex flex-col gap-2 pt-1" data-testid="reminder-preview">
            <span className="text-xs font-semibold tracking-wider text-mist-500 uppercase">{t('reminders.area.preview')}</span>
            <div className="flex max-w-md flex-col gap-2.5 rounded-xl border border-ink-700 bg-ink-800 px-3 py-2.5">
              <div className="flex items-start gap-2.5">
                <LogoMark size={30} />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-[13.5px] font-semibold" data-testid="reminder-preview-title">
                    {settings.preview.title}
                  </span>
                  <span className="text-[13px] leading-snug text-mist-300" data-testid="reminder-preview-text">
                    {settings.preview.text}
                  </span>
                </span>
              </div>
              <div aria-hidden className="flex justify-end gap-2 text-[12.5px]">
                <span className="nt-btn nt-btn-ghost h-8 px-3 text-[12.5px]">{settings.preview.snooze}</span>
                <span className="nt-btn nt-btn-accent h-8 px-3 text-[12.5px]">{settings.preview.open}</span>
              </div>
            </div>
          </div>
        </Card>

        <Card icon={CalendarDays} title={t('reminders.area.defaults')} text={t('reminders.area.defaultsText')}>
          <ul className="divide-y divide-ink-700" data-testid="reminder-defaults">
            {settings.calendars.map((calendar) => (
              <DefaultRow key={calendar.id} calendar={calendar} onChoose={(choice) => void step(() => remindersApi.setDefault(calendar.id, choice))} />
            ))}
          </ul>
        </Card>

        <Card icon={Smartphone} title={t('reminders.area.devices')}>
          <ul className="space-y-2" data-testid="reminder-devices">
            {settings.browsers.map((each) => (
              <li key={each.id} className="flex items-center gap-2.5 text-[14px]">
                <span aria-hidden className={'h-2 w-2 shrink-0 rounded-full ' + (each.reminders && !each.quiet ? 'bg-ok-500' : 'bg-ink-600')} />
                <span className="min-w-0 flex-1 truncate">{each.this ? t('notify.thisBrowser') : browserName(each, t)}</span>
                <span className="text-[12.5px] text-mist-500">{each.quiet ? t('reminders.area.quiet') : each.reminders ? t('reminders.area.reminds') : t('reminders.area.off')}</span>
              </li>
            ))}
          </ul>
        </Card>
        {problem && (
          <p role="alert" className="text-sm text-bad-500">
            {problem}
          </p>
        )}
    </div>
  )
}

function choiceLabel(choice: string, calendar: ReminderCalendar, t: (key: string, values?: Record<string, unknown>) => string): string {
  if (choice === 'none') return t('reminders.area.none')
  if (choice === 'source') return t('reminders.area.source', { name: t(`reminders.area.sources.${calendar.kind}`) })
  return reminderLabel(Number(choice), t)
}

function DefaultRow({ calendar, onChoose }: { calendar: ReminderCalendar; onChoose: (choice: string) => void }) {
  const { t } = useTranslation()
  const name = calendar.owner ? `${calendar.name} · ${calendar.owner}` : calendar.name
  return (
    <li className="flex items-center gap-3 py-2.5 text-sm" data-testid="reminder-default">
      <Swatch color={calendar.color} hollow={calendar.hidden} />
      <span className="min-w-0 flex-1 truncate text-mist-100" title={name}>
        {name}
      </span>
      <select
        value={calendar.choice}
        onChange={(event) => onChoose(event.target.value)}
        aria-label={t('reminders.area.defaultFor', { name: calendar.name })}
        className="nt-field h-9 w-auto max-w-[12rem]"
        data-testid={`reminder-default-${calendar.id}`}
      >
        {calendar.options.map((choice) => (
          <option key={choice} value={choice}>
            {choiceLabel(choice, calendar, t)}
          </option>
        ))}
      </select>
    </li>
  )
}

/** What this browser can do: "Mitteilungen erlauben" before the browser asks (EC-4), the way to allow them when it
 * blocks them (EC-2), and on an iPhone's Safari that only the installed web app takes them (EC-5). */
function BrowserHint({ state, onAllow }: { state: BrowserState | null; onAllow: () => void }) {
  const { t } = useTranslation()
  if (state === 'off')
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-ink-700 bg-[var(--color-accent-wash)] px-4 py-3 text-sm" data-testid="reminders-allow-hint">
        <span className="text-mist-200">{t('reminders.area.deviceFirst')}</span>
        <button type="button" className="nt-btn nt-btn-accent h-9 text-sm" onClick={onAllow} data-testid="reminders-allow">
          {t('notify.allow')}
        </button>
      </div>
    )
  if (state === 'denied')
    return (
      <p className="rounded-[12px] border border-warn-500/30 bg-[var(--color-warn-wash)] px-4 py-3 text-sm text-warn-500" role="status" data-testid="reminders-denied">
        {t('notify.deniedHelp')}
      </p>
    )
  if (state === 'unsupported')
    return (
      <p className="rounded-[12px] border border-ink-700 px-4 py-3 text-sm text-mist-300" role="status" data-testid="reminders-unsupported">
        {t('notify.unsupportedHelp')}
      </p>
    )
  return null
}

function browserName(each: ReminderBrowser, t: (key: string, values?: Record<string, string>) => string): string {
  if (each.installed) return t('notify.webApp', { system: each.system || t('notify.device') })
  if (each.browser && each.system) return t('notify.browserOn', { browser: each.browser, system: each.system })
  return each.browser || each.system || t('notify.otherBrowser')
}
