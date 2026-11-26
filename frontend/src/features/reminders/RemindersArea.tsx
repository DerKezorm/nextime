/**
 * Settings › My account › "Erinnerungen" (TIME-7 AC-6, AC-7, AC-8, T7-Erinnerung), in the cards and checkbox rows of
 * the settings as nexnotes lays them out: "Auf diesem Gerät erinnern" (this browser only) and "Nicht doppelt, wenn
 * Apple Kalender schon erinnert" with its sentence; "Vorgabe je Kalender" with color, name and choice per calendar
 * ("wie in iCloud" for connected ones); the devices that remind; what keeps reminders from arriving (a browser that
 * blocks them, Safari outside the installed web app, no HTTPS, no internet; EC-2, EC-4, EC-5, EC-15); and "So sieht
 * die Erinnerung aus" with "In 5 Min erneut" and "Öffnen". The browser's permission and sign-up are the ones of TIME-21
 * (`features/notify/browser.ts`).
 */
/**
 * Settings › My account › "Benachrichtigungen" (plan "Einstellungen", E1; before "Erinnerungen", the old address leads
 * here; TIME-7 AC-6, AC-7, AC-8, T7-Erinnerung). First "Auf welchem Weg" of the ui kit (`NotifyWays`): this browser (on
 * or off, "Mitteilungen einschalten" / "Ausschalten", "Probemeldung schicken", what keeps it from working: a browser that
 * blocks them, Safari outside the installed web app; EC-2, EC-4, EC-5), "Browser mit Mitteilungen" with "Entfernen" for
 * each of the account's browsers, and the iPhone and iPad (under Connections). nextime mails no
 * notifications, so there is no way "Mail". Then nextime's own cards: "Erinnerungen" ("Auf diesem Gerät erinnern",
 * "Nicht doppelt, wenn Apple Kalender schon erinnert", what keeps reminders from arriving: no device, no HTTPS, no
 * internet, EC-15; "So sieht die Erinnerung aus"), "Vorgabe je Kalender" with color, name and choice per calendar
 * ("wie in iCloud" for connected ones), the devices that remind. The browser's permission and sign-up are the ones of
 * TIME-21 (`features/notify/browser.ts`). Built from the ui kit.
 */
import { Bell, CalendarDays, Smartphone } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { LogoMark } from '../../components/Logo'
import { problemText } from '../../lib/errors'
import { Badge, Card, CheckboxRow, Notice, NotifyWays, Row, Rows } from '../../vendor/nexui'
import { reminderLabel } from '../calendars/labels'
import { Swatch } from '../calendars/Swatch'
import { notifyApi } from '../notify/api'
import { allowBrowser, browserState, keepRemindHere, stopBrowser, type BrowserState } from '../notify/browser'
import { remindersApi, type ReminderBrowser, type ReminderCalendar, type ReminderSettings } from './api'

export function RemindersArea() {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<ReminderSettings | null>(null)
  const [browser, setBrowser] = useState<BrowserState | null>(null)
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)
  const [probing, setProbing] = useState(false)
  const [done, setDone] = useState('')
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
    setBusy(true)
    setProblem('')
    setDone('')
    try {
      const answer = await work()
      if (answer) setSettings(answer)
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      running.current = false
      setBusy(false)
    }
  }

  if (!settings) return problem ? <Notice tone="danger">{problem}</Notice> : null

  const here = settings.this_browser
  const reminding = settings.browsers.filter((each) => each.reminders && !each.quiet)
  const hint =
    browser === 'denied' ? (
      <span data-testid="reminders-denied">{t('notify.deniedHelp')}</span>
    ) : browser === 'unsupported' ? (
      <span data-testid="reminders-unsupported">{t('notify.unsupportedHelp')}</span>
    ) : browser === 'off' ? (
      <span data-testid="reminders-allow-hint">{t('reminders.area.deviceFirst')}</span>
    ) : undefined
  return (
    <div className="flex min-w-0 flex-col gap-6" data-testid="reminders-area">
      <NotifyWays
        browser={{
          on: browser === 'on',
          busy,
          disabled: browser === 'denied' || browser === 'unsupported',
          note: hint,
          onTurnOn: () => void step(async () => (setBrowser(await allowBrowser()), remindersApi.get())),
          onTurnOff: () => void step(async () => (setBrowser(await stopBrowser()), remindersApi.get())),
          onProbe: here.subscribed
            ? () =>
                void step(async () => {
                  setProbing(true)
                  try {
                    await notifyApi.probe()
                    setDone(t('notify.probeSent'))
                  } finally {
                    setProbing(false)
                  }
                })
            : undefined,
          probing,
        }}
        browsers={{
          items: settings.browsers.map((each) => ({ id: String(each.id), name: browserName(each, t), current: each.this })),
          busy,
          onRemove: (id) =>
            void step(async () => {
              const leaving = settings.browsers.find((each) => String(each.id) === id)
              // This browser's own sign-up goes the way "Ausschalten" goes, so the browser forgets it too.
              if (leaving?.this) setBrowser(await stopBrowser())
              else await notifyApi.remove(Number(id))
              return remindersApi.get()
            }),
        }}
        devices={{ href: '/settings/connections' }}
      />
      {done && <Notice tone="ok">{done}</Notice>}

      <Card id="reminders" icon={Bell} title={t('settings.areas.reminders.title')}>
        <CheckboxRow
          label={t('reminders.area.device')}
          hint={here.subscribed ? t('reminders.area.deviceHint') : t('reminders.area.deviceFirst')}
          checked={here.subscribed && here.reminders}
          disabled={!here.subscribed}
          onChange={(on) => void step(async () => (keepRemindHere(on), remindersApi.device(on)))}
          testId="reminders-device"
        />
        <CheckboxRow
          label={t('reminders.area.notDouble')}
          hint={t('reminders.area.notDoubleHint')}
          checked={settings.not_double}
          onChange={(on) => void step(() => remindersApi.notDouble(on))}
          testId="reminders-not-double"
        />
        {(reminding.length === 0 || !settings.delivery.https || !settings.delivery.internet) && (
          <div role="status" data-testid="reminders-warning">
            <Notice tone="warn">
              <span className="flex flex-col gap-1">
                {reminding.length === 0 && <span data-testid="reminders-no-device">{t('reminders.area.noDevice')}</span>}
                {!settings.delivery.https && <span data-testid="reminders-no-https">{t('notify.noHttps')}</span>}
                {!settings.delivery.internet && <span data-testid="reminders-no-internet">{t('notify.noInternet')}</span>}
              </span>
            </Notice>
          </div>
        )}
        <div className="flex flex-col gap-2 pt-1" data-testid="reminder-preview">
          <span className="text-xs font-semibold tracking-wider text-[var(--nexui-text-muted)] uppercase">{t('reminders.area.preview')}</span>
          <div className="flex max-w-md flex-col gap-2.5 rounded-[12px] border border-solid border-[var(--nexui-line)] bg-[var(--nexui-surface-raised)] px-3 py-2.5">
            <div className="flex items-start gap-2.5">
              <LogoMark size={30} />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="text-[13.5px] font-semibold" data-testid="reminder-preview-title">
                  {settings.preview.title}
                </span>
                <span className="text-[13px] leading-snug text-[var(--nexui-text-muted)]" data-testid="reminder-preview-text">
                  {settings.preview.text}
                </span>
              </span>
            </div>
            {/* The two actions of the notification as the system draws them: a picture, no buttons of the page. */}
            <div aria-hidden className="flex justify-end gap-3 text-[12.5px] font-medium text-[var(--nexui-accent-text)]">
              <span>{settings.preview.snooze}</span>
              <span>{settings.preview.open}</span>
            </div>
          </div>
        </div>
      </Card>

      <Card id="reminder-defaults" icon={CalendarDays} title={t('reminders.area.defaults')} subtitle={t('reminders.area.defaultsText')}>
        <Rows label={t('reminders.area.defaults')} testId="reminder-defaults">
          {settings.calendars.map((calendar) => (
            <DefaultRow key={calendar.id} calendar={calendar} onChoose={(choice) => void step(() => remindersApi.setDefault(calendar.id, choice))} />
          ))}
        </Rows>
      </Card>

      <Card id="reminder-devices" icon={Smartphone} title={t('reminders.area.devices')}>
        <Rows label={t('reminders.area.devices')} testId="reminder-devices">
          {settings.browsers.map((each) => (
            <Row
              key={each.id}
              icon="Monitor"
              name={each.this ? t('notify.thisBrowser') : browserName(each, t)}
              badge={
                <Badge tone={each.reminders && !each.quiet ? 'on' : 'off'}>
                  {each.quiet ? t('reminders.area.quiet') : each.reminders ? t('reminders.area.reminds') : t('reminders.area.off')}
                </Badge>
              }
            />
          ))}
        </Rows>
      </Card>
      {problem && <Notice tone="danger">{problem}</Notice>}
    </div>
  )
}

function choiceLabel(choice: string, calendar: ReminderCalendar, t: (key: string, values?: Record<string, unknown>) => string): string {
  if (choice === 'none') return t('reminders.area.none')
  if (choice === 'source')
    return t('reminders.area.source', {
      name: t(`reminders.area.sources.${calendar.kind}`),
    })
  return reminderLabel(Number(choice), t)
}

function DefaultRow({ calendar, onChoose }: { calendar: ReminderCalendar; onChoose: (choice: string) => void }) {
  const { t } = useTranslation()
  const name = calendar.owner ? `${calendar.name} · ${calendar.owner}` : calendar.name
  return (
    <Row
      testId="reminder-default"
      lead={<Swatch color={calendar.color} hollow={calendar.hidden} />}
      name={name}
      action={
        <select
          value={calendar.choice}
          onChange={(event) => onChoose(event.target.value)}
          aria-label={t('reminders.area.defaultFor', { name: calendar.name })}
          className="h-8 max-w-[12rem] rounded-[9px] border border-solid border-[var(--nexui-edge)] bg-[var(--nexui-field)] px-2 text-[13px] text-[var(--nexui-text)]"
          data-testid={`reminder-default-${calendar.id}`}
        >
          {calendar.options.map((choice) => (
            <option key={choice} value={choice}>
              {choiceLabel(choice, calendar, t)}
            </option>
          ))}
        </select>
      }
    />
  )
}

function browserName(each: ReminderBrowser, t: (key: string, values?: Record<string, string>) => string): string {
  if (each.installed) return t('notify.webApp', { system: each.system || t('notify.device') })
  if (each.browser && each.system)
    return t('notify.browserOn', {
      browser: each.browser,
      system: each.system,
    })
  return each.browser || each.system || t('notify.otherBrowser')
}
