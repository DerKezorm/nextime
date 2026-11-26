/**
 * Settings, My account, Connections: the card "Geräte" (TIME-29 AC-4, AC-7; plan "Einstellungen", Verbindungen 1, E4),
 * the first card there as in every nex app. Every connected iPhone and iPad with its name, its kind, its last contact
 * and whether notifications are on, and "Trennen": the device reaches nothing from that moment on. "iPhone oder iPad
 * verbinden" opens the QR code (AC-1). The calendar account (TIME-20) stays under "Auf iPhone und Co".
 */
import { Laptop, Smartphone, Tablet } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { problemText } from '../../lib/errors'
import { ago } from '../../lib/time'
import { Badge, Button, Card, ConfirmDialog, Notice, Row, Rows } from '../../vendor/nexui'
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
    <Card id="devices" icon="Smartphone" title={t('appDevices.section.title')} subtitle={t('appDevices.section.text')} testId="app-devices">
      {problem && <Notice tone="danger">{problem}</Notice>}
      {devices && devices.length === 0 && (
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]" data-testid="app-devices-none">
          {t('appDevices.section.none')}
        </p>
      )}
      {devices && devices.length > 0 && (
        <Rows label={t('appDevices.section.title')} testId="app-devices-list">
          {devices.map((device) => (
            <Row
              key={device.id}
              testId="app-device-row"
              icon={ICONS[device.kind] ?? Smartphone}
              name={<span data-testid="app-device-name">{device.name}</span>}
              badge={<Badge tone={device.push ? 'on' : 'off'}>{device.push ? t('appDevices.section.push') : t('appDevices.section.pushOff')}</Badge>}
              sub={
                <span data-testid="app-device-meta">
                  {[
                    t(`appDevices.kind.${device.kind === 'mac' ? 'mac' : 'ios'}`),
                    device.last_seen_at ? t('appDevices.section.lastSeen', { when: ago(new Date(device.last_seen_at).getTime(), i18n.language) }) : t('appDevices.section.neverSeen'),
                  ].join(' · ')}
                </span>
              }
              action={
                <Button kind="danger" size="sm" icon="Unplug" onClick={() => setLeaving(device)} testId="app-device-disconnect">
                  {t('appDevices.section.disconnect')}
                </Button>
              }
            />
          ))}
        </Rows>
      )}
      <div>
        <Button kind="primary" icon="Smartphone" onClick={() => setConnecting(true)} testId="app-devices-connect">
          {t('appDevices.section.connect')}
        </Button>
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
        <ConfirmDialog
          title={t('appDevices.section.disconnectTitle', { name: leaving.name })}
          text={t('appDevices.section.disconnectText')}
          cancel={t('common.cancel')}
          closeLabel={t('common.close')}
          confirm={{ label: t('appDevices.section.disconnect'), kind: 'danger', icon: 'Unplug' }}
          onCancel={() => setLeaving(null)}
          onConfirm={() => void disconnect(leaving)}
        />
      )}
    </Card>
  )
}
