/**
 * Settings › My account › Connections: "nexpaper", under the card of nextasks and built like it. Not connected, it
 * says how; address and token are checked at once and the card says "verbunden" with the address; the token can be
 * changed, or nexpaper disconnected after asking. A token run out, one that does not read or a nexpaper without
 * deadlines says so here.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { useDevicePref } from '../../lib/devicePref'
import { errorText, problemText } from '../../lib/errors'
import { Badge, Button, Buttons, Collapsible, ConfirmDialog, Field, Notice, useNexuiTexts } from '../../vendor/nexui'
import { useOffline } from '../offline/connection'
import { nexpaperApi, setNexpaper } from './api'
import { useNexpaper } from './hooks'

/** ``bare``: only the inside, for the place "Von Hand verbinden" of the shared module verbinden's card (the module's
 * card is the frame then); without, the card of its own as before. */
export function NexpaperSection({ bare = false }: { bare?: boolean } = {}) {
  const { t } = useTranslation()
  const status = useNexpaper()
  const offline = useOffline()
  const [editing, setEditing] = useState(false)
  // Folded from the start once connected; open while there is something to read: just connected here, a problem.
  const [fresh, setFresh] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [problem, setProblem] = useState('')
  const kit = useNexuiTexts()
  const [folded, setFolded] = useDevicePref('fold.nexpaper', (raw) => raw !== '0')
  const foldable = Boolean(status?.connected) && !editing && !fresh && !status?.problem

  const disconnect = async () => {
    setProblem('')
    try {
      await nexpaperApi.disconnect()
      setNexpaper({ connected: false, local_allowed: Boolean(status?.local_allowed) })
    } catch (error) {
      setProblem(problemText(error))
    }
    setLeaving(false)
  }

  const inside = (
    <>
      {problem && <Notice tone="danger">{problem}</Notice>}
      {status?.connected && !editing && (
        <>
          <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="nexpaper-connected">
            <Badge tone="on">{t('nexpaper.section.connected')}</Badge>
            <span className="min-w-0 text-[var(--nexui-text-muted)]" data-testid="nexpaper-address">
              {status.host} · {t('nexpaper.section.shows')}
            </span>
          </p>
          {status.problem && (
            <div role="alert" data-testid="nexpaper-problem">
              <Notice tone="warn">{errorText(status.problem, {})}</Notice>
            </div>
          )}
          <Buttons>
            <Button kind="secondary" icon="KeyRound" disabled={offline} onClick={() => setEditing(true)} testId="nexpaper-change">
              {t('nexpaper.section.changeKey')}
            </Button>
            <Button kind="danger" icon="Unplug" disabled={offline} onClick={() => setLeaving(true)} testId="nexpaper-disconnect">
              {t('nexpaper.section.disconnect')}
            </Button>
          </Buttons>
        </>
      )}
      {status && (!status.connected || editing) && (
        <ConnectForm
          url={status.url ?? ''}
          localAllowed={status.local_allowed}
          changing={status.connected}
          disabled={offline}
          onCancel={status.connected ? () => setEditing(false) : undefined}
          onDone={() => (setEditing(false), setFresh(true))}
        />
      )}
      {leaving && (
        <ConfirmDialog
          title={t('nexpaper.section.disconnectTitle')}
          text={t('nexpaper.section.disconnectText')}
          confirm={{ label: t('nexpaper.section.disconnect'), kind: 'danger', icon: 'Unplug' }}
          cancel={t('common.cancel')}
          closeLabel={kit.close}
          onCancel={() => setLeaving(false)}
          onConfirm={() => void disconnect()}
          testId="nexpaper-disconnect-dialog"
        />
      )}
    </>
  )
  if (bare) {
    return (
      <div className="flex flex-col gap-4" data-testid="nexpaper-card">
        {inside}
      </div>
    )
  }
  return (
    <Collapsible
      variant="card"
      id="nexpaper"
      logo="/apps/nexpaper.svg"
      title={t('nexpaper.section.title')}
      subtitle={t('nexpaper.section.text')}
      open={!foldable || !folded}
      onOpenChange={(open) => setFolded(open ? '0' : null)}
      status={
        status?.connected ? (
          <span className="flex min-w-0 items-center gap-2" data-testid="nexpaper-summary">
            <Badge tone="on">{t('nexpaper.section.connected')}</Badge>
            <span className="min-w-0 truncate text-sm text-[var(--nexui-text-muted)]">{status.host}</span>
          </span>
        ) : undefined
      }
      testId="nexpaper-card"
    >
      {inside}
    </Collapsible>
  )
}

/** What nextime knows of a connection made by a click with nexpaper, open in the module's card (plan V23): what went
 * wrong last. Nothing while not connected or all is well. */
export function NexpaperChoices() {
  const status = useNexpaper()
  if (!status?.connected || !status.problem) return null
  return (
    <div role="alert" data-testid="nexpaper-problem">
      <Notice tone="warn">{errorText(status.problem, {})}</Notice>
    </div>
  )
}

function ConnectForm({ url: before, localAllowed, changing, disabled, onCancel, onDone }: { url: string; localAllowed: boolean; changing: boolean; disabled: boolean; onCancel?: () => void; onDone: () => void }) {
  const { t } = useTranslation()
  const [url, setUrl] = useState(before)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState('')
  const [ownNetwork, setOwnNetwork] = useState(false)

  const connect = async () => {
    if (busy || !url.trim() || !token.trim()) return
    setBusy(true)
    setProblem('')
    setOwnNetwork(false)
    try {
      setNexpaper(await nexpaperApi.connect(url.trim(), token.trim()))
      setToken('')
      onDone()
    } catch (error) {
      setProblem(problemText(error))
      setOwnNetwork(error instanceof ApiError && error.code === 'nexpaper_address_local')
    }
    setBusy(false)
  }

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        void connect()
      }}
      data-testid="nexpaper-form"
    >
      {!changing && (
        <p className="text-sm text-mist-300" data-testid="nexpaper-how">
          {t('nexpaper.section.how')}
        </p>
      )}
      <Field label={t('nexpaper.section.url')} value={url} onChange={(event) => setUrl(event.target.value)} type="url" maxLength={500} hints={[t('nexpaper.section.urlHint')]} data-testid="nexpaper-url" />
      <Field label={t('nexpaper.section.key')} value={token} onChange={(event) => setToken(event.target.value)} type="password" autoComplete="off" maxLength={500} hints={[t('nexpaper.section.keyHint')]} data-testid="nexpaper-key" />
      {!localAllowed && (
        <p className="text-xs text-mist-500" data-testid="nexpaper-local">
          {t('nexpaper.section.localOff')}
        </p>
      )}
      {problem && <Notice tone="danger">{problem}</Notice>}
      {ownNetwork && (
        <Link to="/settings/general#switches" className="block text-sm text-accent-400 hover:underline" data-testid="nexpaper-local-switch">
          {t('nexpaper.section.toSwitch')}
        </Link>
      )}
      <Buttons end>
        {onCancel && (
          <Button kind="secondary" icon="X" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
        )}
        <Button kind="primary" icon="Plug" type="submit" busy={busy} disabled={disabled || !url.trim() || !token.trim()} testId="nexpaper-connect">
          {busy ? t('nexpaper.section.checking') : t('nexpaper.section.connect')}
        </Button>
      </Buttons>
    </form>
  )
}
