/**
 * Settings, My account, On iPhone and co: the card "Geräte" (TIME-29 AC-4, AC-7), as nexnotes has it under Connections.
 * Every connected iPhone and iPad with its name, its kind, its last contact and whether notifications are on, and
 * "Trennen": the device reaches nothing from that moment on. "iPhone oder iPad verbinden" opens the QR code (AC-1).
 * The calendar account (TIME-20) follows below it in the same area.
 */
import { Laptop, Smartphone, Tablet } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '../../components/Dialog'
import { Problem } from '../../components/Field'
import { problemText } from '../../lib/errors'
import { ago } from '../../lib/time'
import { Card } from '../../pages/settings/ui'
import { appDevicesApi, type AppDevice } from './api'
import { ConnectDialog } from './ConnectDialog'

const ICONS = { ios: Smartphone, ipados: Tablet, mac: Laptop }

export function AppDevicesSection() {
  const { t, i18n } = useTranslation()
  const [devices, setDevices] = useState<AppDevice[] | null>(null)
  const [problem, setProblem] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [leaving, setLeaving] = useState<AppDevice | null>(null)

  const load = useCallback(() => appDevicesApi.list().then(setDevices, (error) => setProblem(problemText(error))), [])
  useEffect(() => {
    void load()
  }, [load])

  const disconnect = async (device: AppDevice) => {
    setProblem('')
    try {
      await appDevicesApi.disconnect(device.id)
    } catch (error) {
      setProblem(problemText(error))
    }
    setLeaving(null)
    await load()
  }

  return (
    <Card id="app-devices" icon={Smartphone} title={t('appDevices.section.title')} text={t('appDevices.section.text')}>
      <Problem text={problem} />
      {devices && devices.length === 0 && (
        <p className="text-sm text-mist-500" data-testid="app-devices-none">
          {t('appDevices.section.none')}
        </p>
      )}
      {devices && devices.length > 0 && (
        <ul className="flex flex-col" data-testid="app-devices-list">
          {devices.map((device) => {
            const Icon = ICONS[device.kind] ?? Smartphone
            const meta = [
              t(`appDevices.kind.${device.kind === 'mac' ? 'mac' : 'ios'}`),
              device.last_seen_at ? t('appDevices.section.lastSeen', { when: ago(new Date(device.last_seen_at).getTime(), i18n.language) }) : t('appDevices.section.neverSeen'),
              ...(device.push ? [t('appDevices.section.push')] : []),
            ]
            return (
              <li key={device.id} className="flex items-center gap-3 border-b border-ink-800 py-2.5 last:border-0" data-testid="app-device-row">
                <Icon className="h-5 w-5 shrink-0 text-mist-300" strokeWidth={1.8} aria-hidden />
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm text-mist-100" data-testid="app-device-name">
                    {device.name}
                  </span>
                  <span className="text-xs text-mist-500" data-testid="app-device-meta">
                    {meta.join(' · ')}
                  </span>
                </span>
                <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setLeaving(device)} data-testid="app-device-disconnect">
                  {t('appDevices.section.disconnect')}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      <div>
        <button type="button" className="nt-btn nt-btn-accent" onClick={() => setConnecting(true)} data-testid="app-devices-connect">
          {t('appDevices.section.connect')}
        </button>
      </div>
      {connecting && (
        <ConnectDialog
          onClose={() => {
            setConnecting(false)
            void load()
          }}
        />
      )}
      {leaving && (
        <Dialog title={t('appDevices.section.disconnectTitle', { name: leaving.name })} onClose={() => setLeaving(null)}>
          <p className="text-sm text-mist-300">{t('appDevices.section.disconnectText')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setLeaving(null)}>
              {t('common.cancel')}
            </button>
            <button type="button" className="nt-btn nt-btn-danger" onClick={() => void disconnect(leaving)} data-testid="app-device-disconnect-confirm">
              {t('appDevices.section.disconnect')}
            </button>
          </div>
        </Dialog>
      )}
    </Card>
  )
}
