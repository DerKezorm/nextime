/**
 * Settings › "Set up Google" (TIME-16), for the operator: the switch for the connection to Google Calendar (the same as
 * under General, AC-5), and the set-up of the server's own Google Cloud project: five steps that tick when done
 * (AC-1, AC-6), the redirect address to enter at Google with "Kopieren", client ID and secret, checked at once, and
 * "Google bereit. n Mitglieder sind verbunden." (AC-7). Changing or removing the values with members connected warns
 * first (EC-2); without HTTPS (EC-4) or after the address of nextime changed (EC-13) the page says so. The secret is
 * never shown again (EC-12).
 */
import { Check, Cloud, KeyRound } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { googleSetupApi, type GoogleSetup } from '../../features/sources/api'
import { copyText } from '../../lib/copy'
import { errorText, problemText } from '../../lib/errors'
import { useServerSettings } from './ServerCards'
import { Button, Buttons, Card, CheckboxRow, ConfirmDialog, Field, Notice } from '../../vendor/nexui'

const STEPS = ['project', 'api', 'consent', 'client', 'values'] as const

export function GoogleArea() {
  const { t } = useTranslation()
  const server = useServerSettings()
  const s = server.settings
  return (
    <div className="space-y-6" data-testid="google-area">
      {s && (
        <Card id="google" icon={Cloud} title={t('server.google')} subtitle={t('server.googleHint')}>
          <CheckboxRow
            label={`${t('server.google')} (${s.google_configured ? t('server.googleSet') : t('server.googleNotSet')})`}
            checked={s.google_enabled}
            disabled={!s.google_configured}
            onChange={(google_enabled) => void server.save({ google_enabled })}
            testId="google-switch"
          />
        </Card>
      )}
      <GoogleSetupCard onChanged={() => void server.reload()} />
    </div>
  )
}

function GoogleSetupCard({ onChanged }: { onChanged: () => void }) {
  const { t } = useTranslation()
  const [setup, setSetup] = useState<GoogleSetup | null>(null)
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [problems, setProblems] = useState<{ client_id?: string; client_secret?: string }>({})
  const [problem, setProblem] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<null | 'save' | 'remove'>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    googleSetupApi.read().then(
      (answer) => {
        if (!answer || typeof answer.redirect_uri !== 'string') return
        setSetup(answer)
        setClientId(answer.client_id)
      },
      (error: unknown) => setProblem(problemText(error)),
    )
  }, [])

  const run = async (work: () => Promise<GoogleSetup>, ask: 'save' | 'remove') => {
    if (busy) return
    setBusy(true)
    setProblems({})
    setProblem(null)
    try {
      const answer = await work()
      setSetup(answer)
      setClientId(answer.client_id)
      setSecret('')
      setConfirming(null)
      onChanged()
    } catch (error) {
      if (error instanceof ApiError && error.code === 'google_change_needs_confirm') setConfirming(ask)
      else if (error instanceof ApiError && (error.values.field === 'client_id' || error.values.field === 'client_secret')) {
        setConfirming(null)
        setProblems({ [String(error.values.field)]: errorText(error.code, error.values) })
      } else {
        setConfirming(null)
        setProblem(problemText(error))
      }
    } finally {
      setBusy(false)
    }
  }
  const save = (confirm: boolean) => run(() => googleSetupApi.save(clientId.trim(), secret.trim(), confirm), 'save')
  const remove = (confirm: boolean) => run(() => (confirm || !setup?.members ? googleSetupApi.remove() : Promise.reject(new ApiError(409, 'google_change_needs_confirm'))), 'remove')

  if (!setup) return problem ? <p role="alert" className="text-sm text-bad-500">{problem}</p> : null
  return (
    <Card id="google-setup" icon={KeyRound} title={t('googleSetup.title')} subtitle={t('googleSetup.text')}>
      {!setup.https && (
        <div role="status" data-testid="google-https">
          <Notice tone="warn">{t('googleSetup.noHttps')}</Notice>
        </div>
      )}
      {setup.redirect_changed && (
        <div role="status" data-testid="google-moved">
          <Notice tone="warn">{t('googleSetup.moved')}</Notice>
        </div>
      )}
      <ol className="space-y-2.5" data-testid="google-steps">
        {STEPS.map((step, index) => (
          <li key={step} className="flex gap-3 text-sm" data-done={setup.ready ? 'true' : 'false'} data-testid="google-step">
            <span className={'grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs font-semibold ' + (setup.ready ? 'bg-accent-500 text-on-accent' : 'bg-ink-700 text-mist-200')} aria-hidden>
              {setup.ready ? <Check className="h-3.5 w-3.5" /> : index + 1}
            </span>
            <span className="min-w-0">
              <strong className="block text-mist-100">{t(`googleSetup.steps.${step}.title`)}</strong>
              <span className="block text-xs leading-relaxed text-mist-500">{t(`googleSetup.steps.${step}.text`)}</span>
            </span>
          </li>
        ))}
      </ol>
      <div className="text-sm">
        <span className="font-medium text-mist-100">{t('googleSetup.redirect')}</span>
        <div className="mt-1 flex min-w-0 items-center gap-2">
          <code className="min-w-0 flex-1 rounded-[9px] border border-ink-700 bg-ink-900 px-3 py-2 font-mono text-xs break-all text-mist-200" data-testid="google-redirect">
            {setup.redirect_uri}
          </code>
          <Button kind="secondary" size="sm" icon={copied ? 'Check' : 'Copy'} onClick={() => void copyText(setup.redirect_uri).then((done) => setCopied(done))} testId="google-copy">
            {copied ? t('googleSetup.copied') : t('googleSetup.copy')}
          </Button>
        </div>
      </div>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          void save(false)
        }}
      >
        <Value label={t('googleSetup.clientId')} value={clientId} onChange={setClientId} problem={problems.client_id} testId="google-client-id" />
        <Value
          label={t('googleSetup.secret')}
          value={secret}
          onChange={setSecret}
          type="password"
          placeholder={setup.secret_set ? t('googleSetup.secretSet') : ''}
          problem={problems.client_secret}
          testId="google-secret"
        />
        {setup.ready && (
          <div role="status" data-testid="google-ready">
            <Notice tone="ok">{t('googleSetup.ready', { count: setup.members })}</Notice>
          </div>
        )}
        {setup.members > 0 && (
          <p className="text-xs text-warn-500" data-testid="google-warning">
            {t('googleSetup.warning')}
          </p>
        )}
        {problem && (
          <Notice tone="danger">{problem}</Notice>
        )}
        <Buttons>
          <Button kind="primary" icon="Check" type="submit" busy={busy} testId="google-save">
            {busy ? t('sources.checking') : t('common.save')}
          </Button>
          {(setup.ready || setup.client_id) && (
            <Button kind="danger" icon="Trash2" disabled={busy} onClick={() => void remove(false)} testId="google-remove">
              {t('googleSetup.remove')}
            </Button>
          )}
        </Buttons>
      </form>
      {confirming && (
        <ConfirmDialog
          title={confirming === 'save' ? t('googleSetup.confirmSave') : t('googleSetup.confirmRemove')}
          text={t('googleSetup.warning')}
          confirm={confirming === 'save' ? { label: t('googleSetup.confirmSaveGo'), kind: 'danger', icon: 'Check' } : { label: t('googleSetup.remove'), kind: 'danger', icon: 'Trash2' }}
          cancel={t('common.cancel')}
          closeLabel={t('common.close')}
          busy={busy}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void (confirming === 'save' ? save(true) : remove(true))}
          testId="google-confirm"
        />
      )}
    </Card>
  )
}

function Value({ label, value, onChange, type = 'text', placeholder, problem, testId }: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  placeholder?: string
  problem?: string
  testId: string
}) {
  return (
    <Field label={label} type={type} value={value} placeholder={placeholder} autoComplete="off" spellCheck={false} onChange={(event) => onChange(event.target.value)} error={problem} data-testid={testId} />
  )
}
