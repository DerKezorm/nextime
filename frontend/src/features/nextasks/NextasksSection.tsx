/**
 * Settings › My account › Connections: "nextasks" (TIME-25, as nexnotes' NOTES-19 card). Not connected, it says how
 * (AC-5); address and key are checked at once and the card says "verbunden" with the address and what it shows
 * (AC-1); the key can be changed, or nextasks disconnected after asking (AC-8). A key run out, one that only reads or
 * another API says so here with the way to a new key (EC-4, EC-5).
 *
 * A `Collapsible` card of the ui kit with nextasks' logo (plan "Einstellungen", Verbindungen 3): connected, it folds to
 * its head ("verbunden" and the address), folded until opened on this device (`fold.nextasks`, as before); just
 * connected, or with a problem, it stays open.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { ApiError } from '../../api/client'

import { useDevicePref } from '../../lib/devicePref'
import { errorText, problemText } from '../../lib/errors'
import { Badge, Button, Buttons, Collapsible, ConfirmDialog, Field, Notice, useNexuiTexts } from '../../vendor/nexui'
import { useOffline } from '../offline/connection'
import { AreaMark } from './AreaMark'
import { nextasksApi, noteChoice, setNextasks, type AreaChoice as AreaChoiceRow } from './api'
import { useNextasks } from './hooks'

/** ``bare``: only the inside, for the place "Von Hand verbinden" of the shared module verbinden's card (the module's
 * card is the frame then); without, the card of its own as before. */
export function NextasksSection({ bare = false }: { bare?: boolean } = {}) {
  const { t } = useTranslation()
  const status = useNextasks()
  const offline = useOffline()
  const [editing, setEditing] = useState(false)
  // Folded from the start once connected; open while there is something to read: just connected here, a problem.
  const [fresh, setFresh] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [problem, setProblem] = useState('')
  const kit = useNexuiTexts()
  const [folded, setFolded] = useDevicePref('fold.nextasks', (raw) => raw !== '0')
  const foldable = Boolean(status?.connected) && !editing && !fresh && !status?.problem

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

  const inside = (
    <>
      {problem && <Notice tone="danger">{problem}</Notice>}
      {status?.connected && !editing && (
        <>
          <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" data-testid="nextasks-connected">
            <Badge tone="on">{t('nextasks.section.connected')}</Badge>
            <span className="min-w-0 text-[var(--nexui-text-muted)]" data-testid="nextasks-address">
              {status.host} · {t('nextasks.section.shows')}
            </span>
          </p>
          {/* In the card of the shared module verbinden (``bare``) these stand open in the card (NextasksChoices, V23). */}
          {!bare && status.problem && (
            <div role="alert" data-testid="nextasks-problem">
              <Notice tone="warn">{errorText(status.problem, {})}</Notice>
            </div>
          )}
          {!bare && !status.problem && status.level === 'read' && (
            <p className="m-0 text-sm text-[var(--nexui-text-muted)]" data-testid="nextasks-read-only">
              {t('nextasks.section.readOnly')}
            </p>
          )}
          {!bare && <AreaChoice disabled={offline} />}
          <Buttons>
            <Button kind="secondary" icon="KeyRound" disabled={offline} onClick={() => setEditing(true)} testId="nextasks-change">
              {t('nextasks.section.changeKey')}
            </Button>
            <Button kind="danger" icon="Unplug" disabled={offline} onClick={() => setLeaving(true)} testId="nextasks-disconnect">
              {t('nextasks.section.disconnect')}
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
          title={t('nextasks.section.disconnectTitle')}
          text={t('nextasks.section.disconnectText')}
          confirm={{ label: t('nextasks.section.disconnect'), kind: 'danger', icon: 'Unplug' }}
          cancel={t('common.cancel')}
          closeLabel={kit.close}
          onCancel={() => setLeaving(false)}
          onConfirm={() => void disconnect()}
          testId="nextasks-disconnect-dialog"
        />
      )}
    </>
  )
  if (bare) {
    return (
      <div className="flex flex-col gap-4" data-testid="nextasks-card">
        {inside}
      </div>
    )
  }
  return (
    <Collapsible
      variant="card"
      id="nextasks"
      logo="/apps/nextasks.svg"
      title={t('nextasks.section.title')}
      subtitle={t('nextasks.section.text')}
      open={!foldable || !folded}
      onOpenChange={(open) => setFolded(open ? '0' : null)}
      status={
        status?.connected ? (
          <span className="flex min-w-0 items-center gap-2" data-testid="nextasks-summary">
            <Badge tone="on">{t('nextasks.section.connected')}</Badge>
            <span className="min-w-0 truncate text-sm text-[var(--nexui-text-muted)]">{status.host}</span>
          </span>
        ) : undefined
      }
      testId="nextasks-card"
    >
      {inside}
    </Collapsible>
  )
}

/** What nextime keeps of its connection with nextasks, open in the module's card (plan V23), by click or by hand: what
 * went wrong last, whether the access only reads, and the choice of areas and lists. Nothing while not connected. */
export function NextasksChoices() {
  const { t } = useTranslation()
  const status = useNextasks()
  const offline = useOffline()
  if (!status?.connected) return null
  return (
    <div className="flex flex-col gap-4" data-testid="nextasks-choices">
      {status.problem && (
        <div role="alert" data-testid="nextasks-problem">
          <Notice tone="warn">{errorText(status.problem, {})}</Notice>
        </div>
      )}
      {!status.problem && status.level === 'read' && (
        <p className="m-0 text-sm text-[var(--nexui-text-muted)]" data-testid="nextasks-read-only">
          {t('nextasks.section.readOnly')}
        </p>
      )}
      <AreaChoice disabled={offline} />
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
      <Field label={t('nextasks.section.url')} value={url} onChange={(event) => setUrl(event.target.value)} type="url" maxLength={500} hints={[t('nextasks.section.urlHint')]} data-testid="nextasks-url" />
      <Field label={t('nextasks.section.key')} value={token} onChange={(event) => setToken(event.target.value)} type="password" autoComplete="off" maxLength={500} hints={[t('nextasks.section.keyHint')]} data-testid="nextasks-key" />
      {!localAllowed && (
        <p className="text-xs text-mist-500" data-testid="nextasks-local">
          {t('nextasks.section.localOff')}
        </p>
      )}
      {problem && <Notice tone="danger">{problem}</Notice>}
      {ownNetwork && (
        <Link to="/settings/server-connections#nex-apps" className="block text-sm text-accent-400 hover:underline" data-testid="nextasks-local-switch">
          {t('nextasks.section.toSwitch')}
        </Link>
      )}
      <Buttons end>
        {onCancel && (
          <Button kind="secondary" icon="X" onClick={onCancel}>
            {t('common.cancel')}
          </Button>
        )}
        <Button kind="primary" icon="Plug" type="submit" busy={busy} disabled={disabled || !url.trim() || !token.trim()} testId="nextasks-connect">
          {busy ? t('nextasks.section.checking') : t('nextasks.section.connect')}
        </Button>
      </Buttons>
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
      {problem && <Notice tone="danger">{problem}</Notice>}
    </fieldset>
  )
}
