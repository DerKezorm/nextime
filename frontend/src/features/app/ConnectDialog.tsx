/**
 * "iPhone oder iPad verbinden" (TIME-29 AC-1), as nexnotes: the QR code with three steps, "Gilt 10 Minuten · neu
 * erzeugen", and the address to type in for a device without a camera (AC-5). The code pairs one device; the dialog asks
 * every two seconds whether it was used and closes itself then. A code that ran out says so and is made anew with one
 * click; closing the dialog withdraws a code nobody used.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Buttons, Dialog, Notice } from '../../vendor/nexui'
import { problemText } from '../../lib/errors'
import { appDevicesApi, qrSource, type Pairing } from './api'

/** How often the dialog asks whether the code was used. */
export const POLL_MS = 2000

export function ConnectDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const [pairing, setPairing] = useState<Pairing | null>(null)
  const [expired, setExpired] = useState(false)
  const [problem, setProblem] = useState('')
  const current = useRef<Pairing | null>(null)
  const used = useRef(false)

  const make = useCallback(async () => {
    setProblem('')
    const before = current.current
    if (before) void appDevicesApi.withdraw(before.id).catch(() => undefined)
    try {
      const made = await appDevicesApi.pair()
      current.current = made
      setPairing(made)
      setExpired(false)
    } catch (error) {
      setProblem(problemText(error))
    }
  }, [])

  useEffect(() => {
    void make()
  }, [make])

  const close = useCallback(() => {
    const left = current.current
    if (left && !used.current) void appDevicesApi.withdraw(left.id).catch(() => undefined)
    onClose()
  }, [onClose])

  useEffect(() => {
    if (!pairing || expired) return
    const timer = window.setInterval(() => {
      if (new Date(pairing.expires_at).getTime() <= Date.now()) {
        setExpired(true)
        return
      }
      appDevicesApi.state(pairing.id).then(
        (found) => {
          if (current.current?.id !== pairing.id) return
          if (found.state === 'used') {
            used.current = true
            onClose()
          } else if (found.state === 'expired') setExpired(true)
        },
        () => undefined,
      )
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [pairing, expired, onClose])

  const host = pairing ? new URL(pairing.address).host : ''
  return (
    <Dialog title={t('appDevices.connect.title')} onClose={close} closeLabel={t('common.close')} wide>
      <div className="flex flex-wrap gap-6" data-testid="app-connect-dialog">
        <div className="flex w-[220px] flex-none flex-col items-center gap-2.5">
          <div className="h-[220px] w-[220px] rounded-[12px] bg-white p-3.5">
            {pairing && !expired && <img src={qrSource(pairing.qr_svg)} alt={t('appDevices.connect.qr')} className="h-full w-full" data-testid="app-qr" />}
            {expired && (
              <p className="flex h-full items-center justify-center text-center text-sm text-ink-900" data-testid="app-expired">
                {t('appDevices.connect.expired')}
              </p>
            )}
          </div>
          <span className="text-xs text-[var(--nexui-text-muted)]">{t('appDevices.connect.valid')}</span>
          <Button kind="secondary" size="sm" icon="RefreshCw" onClick={() => void make()} testId="app-renew">
            {t('appDevices.connect.renew')}
          </Button>
        </div>
        <div className="flex min-w-[240px] flex-1 flex-col gap-3.5">
          <ol className="m-0 list-decimal space-y-1 pl-5 text-sm leading-relaxed text-[var(--nexui-text)]">
            <li>{t('appDevices.connect.step1')}</li>
            <li>{t('appDevices.connect.step2')}</li>
            <li>{t('appDevices.connect.step3')}</li>
          </ol>
          {pairing && (
            <p className="m-0 rounded-[12px] border border-solid border-[var(--nexui-line)] px-3.5 py-3 text-[13px] leading-relaxed text-[var(--nexui-text-muted)]" data-testid="app-address">
              {t('appDevices.connect.noCamera')} <strong className="text-[var(--nexui-text)]">{host}</strong> {t('appDevices.connect.noCameraEnd')}
            </p>
          )}
          {problem && <Notice tone="danger">{problem}</Notice>}
          <Buttons end>
            <Button kind="secondary" icon="X" onClick={close} testId="app-close">
              {t('common.close')}
            </Button>
          </Buttons>
        </div>
      </div>
    </Dialog>
  )
}
