/**
 * "On iPhone and co" under My account (TIME-20): name a device, get a QR code for the iPhone (ten minutes, once),
 * the profile, and the data to type for Android, Thunderbird and others with the device password shown this once;
 * the list "Your devices" with program and last sync, each to lock out. On an iPhone the same tab is the setup on
 * this phone ("Load profile"); on Android the data to copy (EC-23). Without HTTPS a hint instead (EC-21).
 *
 * The page `/setup/<code>` is where the QR code leads: the same setup on the phone, without signing in.
 */
import { MonitorSmartphone, Smartphone } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { Logo } from '../../components/Logo'
import { copyText } from '../../lib/copy'
import { errorText, problemText } from '../../lib/errors'
import { ago } from '../../lib/time'
import { useTitle } from '../../lib/title'
import { Button, Card, ConfirmDialog } from '../../vendor/nexui'
import { useAuth } from '../../state/auth'
import { browser, devicesApi, profileAddress, type ComingAlong, type Credentials, type DeviceOverview, type NewDevice } from './api'
import { Swatch } from './Swatch'

/** What kind of device the browser runs on: the setup on the phone differs (AC-2, EC-23). */
// eslint-disable-next-line react-refresh/only-export-components
export function deviceKind(agent: string = navigator.userAgent, touch: number = navigator.maxTouchPoints ?? 0): 'apple' | 'android' | 'other' {
  if (/iPhone|iPad|iPod/.test(agent) || (/Macintosh/.test(agent) && touch > 1)) return 'apple'
  if (/Android/.test(agent)) return 'android'
  return 'other'
}

function CopyRow({ label, value, testId }: { label: string; value: string; testId: string }) {
  const { t } = useTranslation()
  const [done, setDone] = useState(false)
  return (
    <div className="grid grid-cols-[7.5rem_1fr_auto] items-center gap-2 text-sm">
      <span className="text-mist-500">{label}</span>
      <span className="min-w-0 font-mono break-all text-mist-100" data-testid={testId}>
        {value}
      </span>
      <Button kind="secondary" size="sm" icon={done ? 'Check' : 'Copy'} onClick={() => void copyText(value).then((ok) => setDone(ok))} aria-label={`${t('common.copy')}: ${label}`}>
        {done ? t('common.copied') : t('common.copy')}
      </Button>
    </div>
  )
}

function rightLabel(right: ComingAlong['right'], t: (key: string) => string): string {
  return right === 'busy' ? t('devices.rights.busy') : right === 'read' ? t('devices.rights.read') : t('devices.rights.edit')
}

function ComingAlongList({ calendars }: { calendars: ComingAlong[] }) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2" data-testid="coming-along">
      <h3 className="text-xs font-semibold tracking-wider text-mist-500 uppercase">{t('devices.comingAlong')}</h3>
      {calendars.length === 0 && <p className="text-sm text-mist-500">{t('devices.noneComing')}</p>}
      <ul className="space-y-1.5">
        {calendars.map((entry) => (
          <li key={entry.id} className="flex items-center gap-2.5 text-sm">
            <Swatch color={entry.color} />
            <span className="min-w-0 flex-1 truncate text-mist-100" title={entry.name}>
              {entry.name}
            </span>
            <span className="text-xs text-mist-500">{rightLabel(entry.right, t)}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-mist-500">{t('devices.notTwice')}</p>
    </div>
  )
}

function Steps({ keys }: { keys: string[] }) {
  const { t } = useTranslation()
  return (
    <ol className="list-decimal space-y-1 pl-5 text-sm text-mist-200">
      {keys.map((key) => (
        <li key={key}>{t(key)}</li>
      ))}
    </ol>
  )
}

/** The setup on the phone itself: name, "Load profile", the three steps after, what comes along (AC-9). */
function PhoneSetup({ defaultName, calendars, onProfile, onData, data, busy, problem }: {
  defaultName: string
  calendars: ComingAlong[]
  onProfile: (name: string) => void
  onData: (name: string) => void
  data: Credentials | null
  busy: boolean
  problem: string | null
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(defaultName)
  const android = deviceKind() === 'android'
  return (
    <div className="mx-auto max-w-md space-y-5" data-testid="phone-setup">
      <h2 className="text-2xl font-bold text-mist-100">{android ? t('devices.phoneTitleAndroid') : t('devices.phoneTitle')}</h2>
      <p className="text-sm text-mist-400">{android ? t('devices.phoneTextAndroid') : t('devices.phoneText')}</p>
      <label className="block text-sm">
        <span className="font-medium text-mist-100">{t('devices.deviceName')}</span>
        <input value={name} onChange={(event) => setName(event.target.value)} className="nt-field mt-1 h-11" data-testid="phone-name" />
      </label>
      {problem && (
        <p role="alert" className="text-sm text-bad-500">
          {problem}
        </p>
      )}
      {data ? (
        <div className="space-y-2 rounded-[12px] border border-ink-700 p-3" data-testid="phone-data">
          <CopyRow label={t('devices.address')} value={data.address} testId="data-address" />
          <CopyRow label={t('devices.user')} value={data.user} testId="data-user" />
          <CopyRow label={t('devices.password')} value={data.password} testId="data-password" />
          <p className="text-xs text-warn-500">{t('devices.onlyNow')}</p>
        </div>
      ) : android ? (
        <Button kind="primary" icon="KeyRound" wide disabled={busy || !name.trim()} onClick={() => onData(name.trim())} testId="phone-show-data">
          {t('devices.showData')}
        </Button>
      ) : (
        <Button kind="primary" icon="Download" wide disabled={busy || !name.trim()} onClick={() => onProfile(name.trim())} testId="load-profile">
          {t('devices.loadProfile')}
        </Button>
      )}
      {!android && (
        <div className="space-y-2 rounded-[12px] border border-ink-700 bg-ink-850 p-4">
          <h3 className="text-xs font-semibold tracking-wider text-mist-500 uppercase">{t('devices.after')}</h3>
          <Steps keys={['devices.phoneStep1', 'devices.phoneStep2', 'devices.phoneStep3']} />
          <p className="text-xs text-mist-500">{t('devices.unsigned')}</p>
        </div>
      )}
      <ComingAlongList calendars={calendars} />
    </div>
  )
}

function DeviceList({ overview, onChanged }: { overview: DeviceOverview; onChanged: () => void }) {
  const { t, i18n } = useTranslation()
  const [asking, setAsking] = useState<{ id: number; name: string } | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const lockOut = async () => {
    if (!asking) return
    try {
      await devicesApi.lockOut(asking.id)
      setAsking(null)
      onChanged()
    } catch (error) {
      setProblem(problemText(error))
    }
  }
  return (
    <Card id="devices-list" icon={MonitorSmartphone} title={t('devices.yours')}>
      <div className="space-y-2" data-testid="device-list">
        {overview.devices.length === 0 ? (
          <div className="nt-empty py-6" data-testid="devices-empty">
            <Smartphone className="h-6 w-6 text-mist-500" strokeWidth={1.6} aria-hidden />
            <p>{t('devices.empty')}</p>
          </div>
        ) : (
          <ul className="overflow-hidden rounded-[12px] border border-ink-700 bg-ink-850">
            {overview.devices.map((device) => (
              <li key={device.id} className="flex items-center gap-3 border-b border-line px-3 py-2.5 last:border-b-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-mist-100" title={device.name}>
                    {device.name}
                  </p>
                  <p className="text-xs text-mist-500" data-testid="device-last">
                    {device.last_used_at
                      ? t('devices.lastSync', { program: device.client || t('devices.unknownProgram'), when: ago(Date.parse(device.last_used_at), i18n.language) })
                      : t('devices.neverSynced')}
                  </p>
                </div>
                <Button kind="danger" size="sm" icon="Ban" onClick={() => setAsking({ id: device.id, name: device.name })} testId="lock-out">
                  {t('devices.lockOut')}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-mist-500">{t('devices.lockOutText')}</p>
        {problem && <p className="text-sm text-bad-500">{problem}</p>}
        {asking && (
          <ConfirmDialog
            title={t('devices.lockOutTitle', { name: asking.name })}
            text={t('devices.lockOutText')}
            confirm={{ label: t('devices.lockOut'), kind: 'danger', icon: 'Ban' }}
            cancel={t('common.cancel')}
            closeLabel={t('common.close')}
            onCancel={() => setAsking(null)}
            onConfirm={() => void lockOut()}
            testId="lock-out-question"
          />
        )}
      </div>
    </Card>
  )
}

export function DevicesTab() {
  const { t } = useTranslation()
  const { me } = useAuth()
  const [overview, setOverview] = useState<DeviceOverview | null>(null)
  const [made, setMade] = useState<NewDevice | null>(null)
  const [data, setData] = useState<Credentials | null>(null)
  const [name, setName] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [here, setHere] = useState(deviceKind() !== 'other')
  const shown = me?.display_name || me?.name || ''
  const load = () =>
    devicesApi
      .list()
      .then(setOverview)
      .catch((error) => setProblem(problemText(error)))
  useEffect(() => {
    void load()
  }, [])

  const create = async (deviceName: string): Promise<NewDevice | null> => {
    setProblem(null)
    if (!deviceName.trim()) {
      setProblem(errorText('device_name_missing'))
      return null
    }
    setBusy(true)
    try {
      const answer = await devicesApi.create(deviceName)
      setOverview((value) => (value ? { ...value, devices: answer.devices } : value))
      return answer
    } catch (error) {
      setProblem(error instanceof ApiError ? problemText(error) : errorText('network'))
      return null
    } finally {
      setBusy(false)
    }
  }

  if (!overview) return problem ? <p className="text-sm text-bad-500">{problem}</p> : <div className="h-32 animate-pulse rounded-[12px] bg-ink-800" aria-hidden />
  if (!overview.https) {
    return (
      <div className="space-y-2 rounded-[12px] border border-warn-500/40 bg-warn-wash p-4 text-sm" data-testid="https-hint">
        <p className="font-medium text-warn-500">{t('devices.httpsTitle')}</p>
        <p className="text-mist-200">{t('devices.httpsText')}</p>
        {me?.role === 'operator' && <p className="text-mist-400">{t('devices.httpsOperator')}</p>}
      </div>
    )
  }
  if (here) {
    return (
      <div className="space-y-8">
        <PhoneSetup
          defaultName={t('devices.defaultName', { name: shown })}
          calendars={overview.calendars}
          busy={busy}
          problem={problem}
          data={data}
          onProfile={async (deviceName) => {
            const answer = await create(deviceName)
            if (answer) browser.go(profileAddress(answer.code, deviceName))
          }}
          onData={async (deviceName) => {
            const answer = await create(deviceName)
            if (answer) setData({ address: answer.address, user: answer.user, password: answer.password, device: answer.device.name })
          }}
        />
        <DeviceList overview={overview} onChanged={() => void load()} />
      </div>
    )
  }
  return (
    <div className="space-y-5" data-testid="devices-tab">
      <div className="flex flex-col gap-5">
        <Card id="devices-new" icon={Smartphone} title={t('devices.addTitle')} subtitle={t('devices.intro')}>
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              void create(name).then((answer) => answer && setMade(answer))
            }}
          >
            <label className="min-w-48 flex-1 text-sm">
              <span className="font-medium text-mist-100">{t('devices.device')}</span>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder={t('devices.defaultName', { name: shown })} className="nt-field mt-1" data-testid="device-name" />
            </label>
            <Button kind="primary" icon="Plus" type="submit" disabled={busy} testId="device-create">
              {t('devices.create')}
            </Button>
          </form>
          {problem && (
            <p role="alert" className="text-sm text-bad-500">
              {problem}
            </p>
          )}
          {made && (
            <div className="space-y-4" data-testid="device-made">
              <div className="flex flex-wrap items-center gap-4">
                <img src={made.qr} alt={t('devices.qr')} className="h-40 w-40 rounded-[9px] bg-white p-1" data-testid="device-qr" />
                <Steps keys={['devices.qrStep1', 'devices.qrStep2', 'devices.qrStep3']} />
              </div>
              <p className="text-xs text-mist-500">
                {t('devices.onThisPhone')} {t('devices.codeValid', { count: made.minutes })}
              </p>
              <div className="space-y-2 border-t border-line pt-3">
                <h3 className="text-sm font-semibold text-mist-100">{t('devices.others')}</h3>
                <CopyRow label={t('devices.address')} value={made.address} testId="data-address" />
                <CopyRow label={t('devices.user')} value={made.user} testId="data-user" />
                <CopyRow label={t('devices.password')} value={made.password} testId="data-password" />
                <p className="text-xs text-warn-500" data-testid="only-now">
                  {t('devices.onlyNow')} {t('devices.noSignInPassword')}
                </p>
              </div>
            </div>
          )}
          {/Macintosh/.test(navigator.userAgent) && (
            <div>
              <Button kind="secondary" icon="Monitor" onClick={() => setHere(true)}>
                {t('devices.setUpHere')}
              </Button>
            </div>
          )}
        </Card>
        <DeviceList overview={overview} onChanged={() => void load()} />
      </div>
    </div>
  )
}

/** Where the QR code leads (`/setup/<code>`): the setup on the phone without signing in (AC-2, AC-13, EC-6). */
export function SetupLinkPage() {
  const { t } = useTranslation()
  const { code = '' } = useParams()
  useTitle(t('devices.phoneTitle'))
  const [page, setPage] = useState<{ device: string; calendars: ComingAlong[] } | null>(null)
  const [refused, setRefused] = useState<string | null>(null)
  const [data, setData] = useState<Credentials | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    devicesApi
      .setup(code)
      .then((answer) => setPage({ device: answer.device, calendars: answer.calendars }))
      .catch((error) => setRefused(error instanceof ApiError ? error.code : 'network'))
  }, [code])
  return (
    <main className="min-h-dvh bg-ink-950 px-4 py-8">
      <div className="mx-auto mb-6 max-w-md">
        <Logo size={28} withWordmark />
      </div>
      {refused && (
        <div className="mx-auto max-w-md rounded-[12px] border border-ink-700 bg-ink-850 p-5 text-sm text-mist-200" role="alert" data-testid="setup-refused">
          {errorText(refused)} {t('devices.makeNew')}
        </div>
      )}
      {page && (
        <PhoneSetup
          defaultName={page.device}
          calendars={page.calendars}
          data={data}
          busy={busy}
          problem={null}
          onProfile={(name) => browser.go(profileAddress(code, name))}
          onData={async (name) => {
            setBusy(true)
            try {
              setData(await devicesApi.credentials(code, name))
            } catch (error) {
              setRefused(error instanceof ApiError ? error.code : 'network')
            } finally {
              setBusy(false)
            }
          }}
        />
      )}
    </main>
  )
}
