/**
 * Settings › My account › Connections: "nextasks" (TIME-25, as nexnotes' NOTES-19 card). Not connected, it says how
 * (AC-5); address and key are checked at once and the card says "verbunden" with the address and what it shows
 * (AC-1); the key can be changed, or nextasks disconnected after asking (AC-8). A key run out, one that only reads or
 * another API says so here with the way to a new key (EC-4, EC-5).
 */
import { ListChecks } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError } from '../../api/client'

import { Dialog } from '../../components/Dialog'
import { Field, Problem } from '../../components/Field'
import { errorText, problemText } from '../../lib/errors'
import { Card } from '../../pages/settings/ui'
import { useOffline } from '../offline/connection'
import { AreaMark } from './AreaMark'
import { nextasksApi, noteChoice, setNextasks, type AreaChoice as AreaChoiceRow } from './api'
import { useNextasks } from './hooks'

export function NextasksSection() {
  const { t } = useTranslation()
  const status = useNextasks()
  const offline = useOffline()
  const [editing, setEditing] = useState(false)
  // Folded from the start once connected; open while there is something to read: just connected here, a problem.
  const [fresh, setFresh] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [problem, setProblem] = useState('')

  const disconnect = async () => {
    setProblem('')
    try {
      await nextasksApi.disconnect()
      setNextasks({ connected: false, local_allowed: Boolean(status?.local_allowed) })
    } catch (error) {
      setProblem(problemText(error))
    }
    setLeaving(false)
  }

  return (
    <Card id="nextasks" foldable={status?.connected && !editing && !fresh && !status.problem ? 'nextasks' : undefined} summary={status?.connected ? (
      <span className="flex min-w-0 items-center gap-2 text-sm" data-testid="nextasks-summary">
        <span className="h-2 w-2 shrink-0 rounded-full bg-ok-500" aria-hidden />
        <span className="font-medium text-ok-500">{t('nextasks.section.connected')}</span>
        <span className="min-w-0 truncate text-mist-300">{status.host}</span>
      </span>
    ) : undefined} icon={ListChecks} logo="/apps/nextasks.svg" title={t('nextasks.section.title')} text={t('nextasks.section.text')}>
      <Problem text={problem} />
      {status?.connected && !editing && (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="nextasks-connected">
            <span className="flex items-center gap-2 font-medium text-ok-500">
              <span className="h-2 w-2 shrink-0 rounded-full bg-ok-500" aria-hidden />
              {t('nextasks.section.connected')}
            </span>
            <span className="min-w-0 text-mist-300" data-testid="nextasks-address">
              {status.host} · {t('nextasks.section.shows')}
            </span>
          </div>
          {status.problem && (
            <p role="alert" className="text-sm text-warn-500" data-testid="nextasks-problem">
              {errorText(status.problem, {})}
            </p>
          )}
          {!status.problem && status.level === 'read' && (
            <p className="text-sm text-mist-500" data-testid="nextasks-read-only">
              {t('nextasks.section.readOnly')}
            </p>
          )}
          <AreaChoice disabled={offline} />
          <div className="flex flex-wrap gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" disabled={offline} onClick={() => setEditing(true)} data-testid="nextasks-change">
              {t('nextasks.section.changeKey')}
            </button>
            <button type="button" className="nt-btn nt-btn-ghost" disabled={offline} onClick={() => setLeaving(true)} data-testid="nextasks-disconnect">
              {t('nextasks.section.disconnect')}
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
        <Dialog title={t('nextasks.section.disconnectTitle')} onClose={() => setLeaving(false)}>
          <p className="text-sm text-mist-300">{t('nextasks.section.disconnectText')}</p>
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setLeaving(false)}>
              {t('common.cancel')}
            </button>
            <button type="button" className="nt-btn nt-btn-danger" onClick={() => void disconnect()} data-testid="nextasks-disconnect-confirm">
              {t('nextasks.section.disconnect')}
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
      // The address without blanks at the edges (EC-6); the server checks it once more.
      setNextasks(await nextasksApi.connect(url.trim(), token.trim()))
      setToken('')
      onDone()
    } catch (error) {
      setProblem(problemText(error))
      // Only the operator hears that the switch for the own network is all that stands in the way, with the way there.
      setOwnNetwork(error instanceof ApiError && error.code === 'nextasks_address_local')
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
      data-testid="nextasks-form"
    >
      {!changing && (
        <p className="text-sm text-mist-300" data-testid="nextasks-how">
          {t('nextasks.section.how')}
        </p>
      )}
      <Field label={t('nextasks.section.url')} value={url} onChange={setUrl} type="url" maxLength={500} hint={t('nextasks.section.urlHint')} />
      <Field label={t('nextasks.section.key')} value={token} onChange={setToken} type="password" autoComplete="off" maxLength={500} hint={t('nextasks.section.keyHint')} />
      {!localAllowed && (
        <p className="text-xs text-mist-500" data-testid="nextasks-local">
          {t('nextasks.section.localOff')}
        </p>
      )}
      <Problem text={problem} />
      {ownNetwork && (
        <Link to="/settings/general#switches" className="block text-sm text-accent-400 hover:underline" data-testid="nextasks-local-switch">
          {t('nextasks.section.toSwitch')}
        </Link>
      )}
      <div className="flex justify-end gap-2">
        {onCancel && (
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onCancel}>
            {t('common.cancel')}
          </button>
        )}
        <button type="submit" className="nt-btn nt-btn-accent" disabled={disabled || busy || !url.trim() || !token.trim()} data-testid="nextasks-connect">
          {busy ? t('nextasks.section.checking') : t('nextasks.section.connect')}
        </button>
      </div>
    </form>
  )
}

/** "Bereiche und Listen" (TIME-25 areas, asked for while testing, 2026-11-23): a tree, each area with a box and its
 * lists below. An area ticked is the whole area, lists added later too (its lists ticked and locked); otherwise single
 * lists, the area's box then half. All areas whole from the start; kept per person. What is not chosen shows nowhere. */
function AreaChoice({ disabled }: { disabled: boolean }) {
  const { t } = useTranslation()
  const [areas, setAreas] = useState<AreaChoiceRow[] | null>(null)
  const [problem, setProblem] = useState('')
  useEffect(() => {
    let live = true
    nextasksApi.areas().then(
      (answer) => live && setAreas(Array.isArray(answer?.areas) ? answer.areas : []),
      (error) => live && setProblem(problemText(error)),
    )
    return () => {
      live = false
    }
  }, [])
  if (!areas?.length && !problem) return null

  const save = async (next: AreaChoiceRow[]) => {
    const before = areas
    setAreas(next)
    setProblem('')
    try {
      const whole = next.filter((area) => area.whole).map((area) => area.id)
      const single = next.filter((area) => !area.whole).flatMap((area) => area.lists.filter((one) => one.chosen).map((one) => one.id))
      setAreas((await nextasksApi.chooseAreas(whole, single)).areas)
      // The sidebar and the views ask again at once: the rows of areas and single lists come with the tasks.
      noteChoice()
    } catch (error) {
      setAreas(before)
      setProblem(problemText(error))
    }
  }
  const pickArea = (area: AreaChoiceRow, on: boolean) =>
    void save(areas!.map((one) => (one.id === area.id ? { ...one, whole: on, lists: one.lists.map((list) => ({ ...list, chosen: on })) } : one)))
  const pickList = (area: AreaChoiceRow, id: string, on: boolean) =>
    void save(areas!.map((one) => (one.id === area.id ? { ...one, lists: one.lists.map((list) => (list.id === id ? { ...list, chosen: on } : list)) } : one)))

  return (
    <fieldset className="space-y-1.5" data-testid="nextasks-areas">
      <legend className="nt-legend">{t('nextasks.section.areas')}</legend>
      <p className="text-xs text-mist-500">{t('nextasks.section.areasHint')}</p>
      {(areas ?? []).map((area) => {
        const some = !area.whole && area.lists.some((one) => one.chosen)
        return (
          <div key={area.id} data-testid="nextasks-area-group">
            <label className="flex items-center gap-2.5 text-sm">
              <input
                type="checkbox"
                className="h-[18px] w-[18px] accent-[var(--color-accent-500)]"
                checked={area.whole}
                ref={(box) => {
                  if (box) box.indeterminate = some
                }}
                aria-checked={some ? 'mixed' : area.whole}
                disabled={disabled}
                onChange={(event) => pickArea(area, event.target.checked)}
                data-testid="nextasks-area"
              />
              <AreaMark area={area} />
              <span className="nt-wrap min-w-0 flex-1 text-mist-100">{area.name}</span>
            </label>
            {area.lists.map((one) => (
              <label key={one.id} className={'mt-1 flex items-center gap-2.5 pl-7 text-sm ' + (area.whole ? 'opacity-60' : '')}>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[var(--color-accent-500)]"
                  checked={area.whole || one.chosen}
                  disabled={disabled || area.whole}
                  onChange={(event) => pickList(area, one.id, event.target.checked)}
                  data-testid="nextasks-list"
                />
                <AreaMark area={one} size={12} />
                <span className="nt-wrap min-w-0 flex-1 text-mist-300">{one.name}</span>
              </label>
            ))}
          </div>
        )
      })}
      {problem && <Problem text={problem} />}
    </fieldset>
  )
}
