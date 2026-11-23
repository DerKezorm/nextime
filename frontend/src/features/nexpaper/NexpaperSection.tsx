/**
 * Settings › My account › Connections: "nexpaper", under the card of nextasks and built like it. Not connected, it
 * says how; address and token are checked at once and the card says "verbunden" with the address; the token can be
 * changed, or nexpaper disconnected after asking. A token run out, one that does not read or a nexpaper without
 * deadlines says so here.
 */
import { FileClock } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { Field, Problem } from '../../components/Field'
import { errorText, problemText } from '../../lib/errors'
import { Card } from '../../pages/settings/ui'
import { useOffline } from '../offline/connection'
import { nexpaperApi, setNexpaper } from './api'
import { useNexpaper } from './hooks'

export function NexpaperSection() {
  const { t } = useTranslation()
  const status = useNexpaper()
  const offline = useOffline()
  const [editing, setEditing] = useState(false)
  // Folded from the start once connected; open while there is something to read: just connected here, a problem.
  const [fresh, setFresh] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [problem, setProblem] = useState('')

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

  return (
    <Card id="nexpaper" foldable={status?.connected && !editing && !fresh && !status.problem ? 'nexpaper' : undefined} summary={status?.connected ? (
      <span className="flex min-w-0 items-center gap-2 text-sm" data-testid="nexpaper-summary">
        <span className="h-2 w-2 shrink-0 rounded-full bg-ok-500" aria-hidden />
        <span className="font-medium text-ok-500">{t('nexpaper.section.connected')}</span>
        <span className="min-w-0 truncate text-mist-300">{status.host}</span>
      </span>
    ) : undefined} icon={FileClock} logo="/apps/nexpaper.svg" title={t('nexpaper.section.title')} text={t('nexpaper.section.text')}>
      <Problem text={problem} />
      {status?.connected && !editing && (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="nexpaper-connected">
            <span className="flex items-center gap-2 font-medium text-ok-500">
              <span className="h-2 w-2 shrink-0 rounded-full bg-ok-500" aria-hidden />
              {t('nexpaper.section.connected')}
            </span>
            <span className="min-w-0 text-mist-300" data-testid="nexpaper-address">
              {status.host} · {t('nexpaper.section.shows')}
            </span>
          </div>
          {status.problem && (
            <p role="alert" className="text-sm text-warn-500" data-testid="nexpaper-problem">
              {errorText(status.problem, {})}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" disabled={offline} onClick={() => setEditing(true)} data-testid="nexpaper-change">
              {t('nexpaper.section.changeKey')}
            </button>
            <button type="button" className="nt-btn nt-btn-ghost" disabled={offline} onClick={() => setLeaving(true)} data-testid="nexpaper-disconnect">
              {t('nexpaper.section.disconnect')}
            </button>
          </div>
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
        <Dialog title={t('nexpaper.section.disconnectTitle')} onClose={() => setLeaving(false)}>
          <p className="text-sm text-mist-300">{t('nexpaper.section.disconnectText')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setLeaving(false)}>
              {t('common.cancel')}
            </button>
            <button type="button" className="nt-btn nt-btn-danger" onClick={() => void disconnect()} data-testid="nexpaper-disconnect-confirm">
              {t('nexpaper.section.disconnect')}
            </button>
          </div>
        </Dialog>
      )}
    </Card>
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
      <Field label={t('nexpaper.section.url')} value={url} onChange={setUrl} type="url" maxLength={500} hint={t('nexpaper.section.urlHint')} />
      <Field label={t('nexpaper.section.key')} value={token} onChange={setToken} type="password" autoComplete="off" maxLength={500} hint={t('nexpaper.section.keyHint')} />
      {!localAllowed && (
        <p className="text-xs text-mist-500" data-testid="nexpaper-local">
          {t('nexpaper.section.localOff')}
        </p>
      )}
      <Problem text={problem} />
      {ownNetwork && (
        <Link to="/settings/general#switches" className="block text-sm text-accent-400 hover:underline" data-testid="nexpaper-local-switch">
          {t('nexpaper.section.toSwitch')}
        </Link>
      )}
      <div className="flex justify-end gap-2">
        {onCancel && (
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onCancel}>
            {t('common.cancel')}
          </button>
        )}
        <button type="submit" className="nt-btn nt-btn-accent" disabled={disabled || busy || !url.trim() || !token.trim()} data-testid="nexpaper-connect">
          {busy ? t('nexpaper.section.checking') : t('nexpaper.section.connect')}
        </button>
      </div>
    </form>
  )
}
