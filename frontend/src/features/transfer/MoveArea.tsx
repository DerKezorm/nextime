/**
 * Settings › My account › "Mitnehmen und umziehen" (TIME-24, drawing "T24-Umzug"), in the cards of the settings as
 * nexnotes lays them out, side by side when wide and one under the other on a phone (EC-15):
 *
 * - "Exportieren": a calendar as an ICS file ("Herunterladen"), or "Alles als ZIP (n Kalender)" with the attachments
 *   beside (AC-1, AC-7). Both are plain downloads of the browser.
 * - "Datei laden": choose an ICS file, see name, count, period and what is there already, choose "nach …" (own
 *   calendars only), "n Termine übernehmen", then the result (AC-2, EC-1, EC-2, EC-6, EC-7, EC-11). Without an own
 *   calendar it says what comes first (EC-16). "Datei laden" in "Kalender hinzufügen" leads here (AC-5).
 * - "Aus iCloud umziehen" (and "Aus Google umziehen"): "Persönlich (iCloud) → Persönlich", "Umziehen", the result with
 *   what did not come and why, and that the source stays as it is (AC-3, AC-4, AC-8, EC-12). A second click while it
 *   runs does nothing, and the same reference makes the server give the same result (EC-10).
 *
 * Loading and moving run as jobs on the server: the page shows how far they are ("1.200 von 21.000") and asks until they
 * are done or failed; leaving the page does not stop them (review of TIME-24, finding 3). Without a connection loading
 * and moving are off (TIME-28 AC-3). Names, titles and reasons are text.
 */
import { ArrowLeftRight, Download, FileUp, Upload } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { errorText, problemText } from '../../lib/errors'
import { Button, Card } from '../../vendor/nexui'
import { newRef, type CalendarEntry } from '../calendars/api'
import { CalendarDialog } from '../calendars/CalendarDialogs'
import { Swatch } from '../calendars/Swatch'
import { announce, useCalendars } from '../calendars/store'
import { useOffline } from '../offline/connection'
import { AddCalendar } from '../sources/AddCalendar'
import { periodText } from './period'
import { follow, transferApi, type Failed, type ImportPreview, type ImportResult, type JobState, type Movable, type MovePreview, type MoveResult } from './api'

export function MoveArea() {
  const { list } = useCalendars()
  return (
    <div className="flex flex-col gap-6" data-testid="move-area">
      <ExportCard calendars={list?.calendars ?? null} />
      <LoadCard calendars={list?.calendars ?? null} />
      <MoveCards />
    </div>
  )
}

function Hint({ tone = 'plain', children, testId }: { tone?: 'plain' | 'ok' | 'warn' | 'bad'; children: ReactNode; testId?: string }) {
  const look =
    tone === 'ok'
      ? 'border-ok-500/30 bg-ok-wash text-mist-100'
      : tone === 'warn'
        ? 'border-warn-500/30 bg-warn-wash text-warn-500'
        : tone === 'bad'
          ? 'border-bad-500/30 bg-bad-wash text-bad-500'
          : 'border-ink-700 bg-[var(--color-accent-wash)] text-mist-200'
  return (
    <div role="status" className={'nt-wrap rounded-[9px] border px-3 py-2 text-sm ' + look} data-testid={testId}>
      {children}
    </div>
  )
}

/** "2 nicht: Anhang über 25 MB." — what did not come, counted by its reason (AC-4, EC-7). */
function failedText(failed: Failed[], t: (key: string, values?: Record<string, unknown>) => string, maxMb: number): string {
  const counts = new Map<string, number>()
  for (const entry of failed) counts.set(entry.reason, (counts.get(entry.reason) ?? 0) + 1)
  return [...counts].map(([reason, count]) => t('move.failed', { count, reason: t(`move.reasons.${reason}`, { max: maxMb }) })).join(' ')
}

/** Whether the card is still on the page: a job that ends after leaving it changes nothing on screen. */
function useMounted() {
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  return mounted
}

/** "Übernimmt … 1.200 von 21.000" while a job runs. */
function Progress({ state, label }: { state: JobState; label: string }) {
  const { t, i18n } = useTranslation()
  const number = (value: number) => new Intl.NumberFormat(i18n.language).format(value)
  return (
    <p className="text-xs text-mist-500" role="status" data-testid="job-progress">
      {t(label, { done: number(state.done), total: number(state.total) })}
    </p>
  )
}

// ---- Exportieren -----------------------------------------------------------------------------------------------------

function ExportCard({ calendars }: { calendars: CalendarEntry[] | null }) {
  const { t } = useTranslation()
  const offline = useOffline()
  const shown = useMemo(() => (calendars ?? []).filter((entry) => entry.group !== 'nextasks'), [calendars])
  const [chosen, setChosen] = useState<number | null>(null)
  const current = chosen ?? shown[0]?.id ?? null
  return (
    <Card icon={Download} title={t('move.export.title')} subtitle={t('move.export.text')} id="move-export">
      <label className="block text-sm">
        <span className="sr-only">{t('move.export.calendar')}</span>
        <select className="nt-field" value={current ?? ''} onChange={(event) => setChosen(Number(event.target.value))} data-testid="export-calendar">
          {shown.map((entry) => (
            <option key={entry.id} value={entry.id}>
              {entry.name}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <Button kind="primary" icon="Download" href={current === null || offline ? undefined : transferApi.exportPath(current)} disabled={current === null || offline} testId="export-download">
          {t('move.export.download')}
        </Button>
        <Button kind="secondary" icon="Archive" href={offline ? undefined : transferApi.zipPath} disabled={offline} testId="export-zip">
          {t('move.export.zip', { count: shown.length })}
        </Button>
      </div>
      <p className="text-xs text-mist-500">{t('move.export.zipNote')}</p>
    </Card>
  )
}

// ---- Datei laden -----------------------------------------------------------------------------------------------------

function LoadCard({ calendars }: { calendars: CalendarEntry[] | null }) {
  const { t, i18n } = useTranslation()
  const offline = useOffline()
  const own = useMemo(() => (calendars ?? []).filter((entry) => entry.group === 'own' && entry.right === 'owner'), [calendars])
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [target, setTarget] = useState<number | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)
  const [making, setMaking] = useState(false)
  const [progress, setProgress] = useState<JobState | null>(null)
  const [newCalendar, setNewCalendar] = useState(false)
  const running = useRef(false)
  const mounted = useMounted()
  const chosenTarget = target ?? own.find((entry) => entry.default)?.id ?? own[0]?.id ?? null

  // What is there already depends on the calendar chosen.
  useEffect(() => {
    if (!preview || chosenTarget === null || preview.state === 'done') return
    let live = true
    transferApi
      .preview(preview.id, chosenTarget)
      .then((answer) => live && setPreview(answer))
      .catch((error) => live && setProblem(problemText(error)))
    return () => {
      live = false
    }
    // Asked again for another file or another calendar, not for its own answer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preview?.id, chosenTarget])

  const choose = async (file: File | undefined) => {
    if (!file) return
    setProblem('')
    setResult(null)
    setPreview(null)
    setBusy(true)
    try {
      setPreview(await transferApi.take(file))
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      setBusy(false)
    }
  }

  const take = async () => {
    if (!preview || chosenTarget === null || running.current) return
    running.current = true
    setMaking(true)
    setProblem('')
    try {
      const id = preview.id
      const ended = await follow(await transferApi.apply(id, chosenTarget), () => transferApi.status(id), (state) => mounted.current && setProgress(state), () => mounted.current)
      if (!mounted.current) return
      if (ended.state === 'done' && ended.result) {
        setResult(ended.result as ImportResult)
        setPreview(null)
        announce()
      } else if (ended.state === 'failed') {
        // Nothing half came in (EC-9): the same file can go again.
        setProblem(errorText(ended.problem ?? 'failed'))
      }
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      running.current = false
      setMaking(false)
      setProgress(null)
    }
  }

  const maxMb = useCalendars().list?.attachment_max_mb ?? 25
  if (calendars !== null && own.length === 0) {
    return (
      <Card icon={Upload} title={t('move.load.title')} subtitle={t('move.load.text')} id="move-load">
        <Hint testId="load-needs-calendar">{t('move.load.needsCalendar')}</Hint>
        <div>
          <Button kind="primary" icon="Plus" disabled={offline} onClick={() => setNewCalendar(true)} testId="load-new-calendar">
            {t('move.load.newCalendar')}
          </Button>
        </div>
        {newCalendar && <CalendarDialog onDone={() => (announce(), setNewCalendar(false))} onClose={() => setNewCalendar(false)} />}
      </Card>
    )
  }

  return (
    <Card icon={Upload} title={t('move.load.title')} subtitle={t('move.load.text')} id="move-load">
      {preview && (
        <div className="space-y-1 rounded-[10px] border border-ink-700 bg-ink-900 p-3" data-testid="load-preview">
          <span className="nt-wrap block text-sm text-mist-100" data-testid="load-name">
            {preview.name}
          </span>
          <span className="block text-xs text-mist-500" data-testid="load-count">
            {t('move.load.count', { count: preview.events, period: periodText(preview.first, preview.last, i18n.language, t('move.load.between')) })}
          </span>
          {preview.existing > 0 && (
            <span className="block text-xs text-mist-500" data-testid="load-existing">
              {t('move.load.existing', { count: preview.existing })}
            </span>
          )}
          {preview.skipped > 0 && (
            <span className="block text-xs text-mist-500" data-testid="load-skipped">
              {t('move.load.skipped', { count: preview.skipped })}
            </span>
          )}
          {preview.too_large > 0 && (
            <span className="block text-xs text-warn-500" data-testid="load-too-large">
              {t('move.load.tooLarge', { count: preview.too_large })}
            </span>
          )}
          {preview.encoding === 'cp1252' && (
            <span className="block text-xs text-mist-500" data-testid="load-encoding">
              {t('move.load.cp1252')}
            </span>
          )}
        </div>
      )}
      <label className={'inline-flex w-fit cursor-pointer text-sm font-medium text-accent-400 hover:underline ' + (offline || busy ? 'pointer-events-none opacity-50' : '')}>
        <FileUp className="mr-1.5 h-4 w-4" aria-hidden />
        {preview ? t('move.load.other') : t('move.load.choose')}
        <input
          type="file"
          accept=".ics,text/calendar"
          className="sr-only"
          disabled={offline || busy}
          onChange={(event) => {
            void choose(event.target.files?.[0])
            event.target.value = ''
          }}
          data-testid="load-file"
        />
      </label>
      {busy && <p className="text-xs text-mist-500">{t('move.load.reading')}</p>}
      {preview && (
        <>
          <label className="block text-sm">
            <span className="sr-only">{t('move.load.target')}</span>
            <select className="nt-field" value={chosenTarget ?? ''} onChange={(event) => setTarget(Number(event.target.value))} data-testid="load-target">
              {own.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {t('move.load.into', { name: entry.name })}
                </option>
              ))}
            </select>
          </label>
          {preview.over_limit && <Hint tone="warn" testId="load-limit">{t('move.limit', { count: preview.account_events + preview.new, limit: preview.limit })}</Hint>}
          <div>
            <Button kind="primary" icon="Upload" busy={making} disabled={offline || preview.new === 0} onClick={() => void take()} testId="load-apply">
              {preview.new === 0 ? t('move.load.nothingNew') : t('move.load.apply', { count: preview.new })}
            </Button>
          </div>
          {progress && <Progress state={progress} label="move.load.running" />}
        </>
      )}
      {result && (
        <Hint tone="ok" testId="load-result">
          {t('move.load.done', { count: result.made, name: result.calendar.name })}
          {result.existing > 0 && ` ${t('move.load.wereThere', { count: result.existing })}`}
          {result.failed.length > 0 && ` ${failedText(result.failed, t, maxMb)}`}
          {(result.shortened ?? 0) > 0 && ` ${t('move.load.shortened', { count: result.shortened })}`}
        </Hint>
      )}
      {problem && (
        <p role="alert" className="text-sm text-bad-500" data-testid="load-problem">
          {problem}
        </p>
      )}
    </Card>
  )
}

// ---- Umziehen --------------------------------------------------------------------------------------------------------

function MoveCards() {
  const { t } = useTranslation()
  const [movable, setMovable] = useState<Movable[] | null>(null)
  const [problem, setProblem] = useState('')
  const [adding, setAdding] = useState(false)
  useEffect(() => {
    let live = true
    transferApi
      .movable()
      .then((answer) => live && setMovable(Array.isArray(answer?.calendars) ? answer.calendars : []))
      .catch((error) => live && setProblem(problemText(error)))
    return () => {
      live = false
    }
  }, [])
  if (movable === null)
    return problem ? (
      <p role="alert" className="text-sm text-bad-500">
        {problem}
      </p>
    ) : null
  const kinds = (['icloud', 'google'] as const).filter((kind) => movable.some((entry) => entry.kind === kind))
  if (kinds.length === 0) {
    return (
      <Card icon={ArrowLeftRight} title={t('move.move.title.icloud')} subtitle={t('move.move.text.icloud')} id="move-move">
        <Hint testId="move-needs-source">{t('move.move.none')}</Hint>
        <div>
          <Button kind="secondary" icon="Plus" onClick={() => setAdding(true)} testId="move-add-calendar">
            {t('calendars.add')}
          </Button>
        </div>
        {adding && <AddCalendar onClose={() => setAdding(false)} />}
      </Card>
    )
  }
  return (
    <div className="space-y-4">
      {kinds.map((kind) => (
        <MoveCard key={kind} kind={kind} calendars={movable.filter((entry) => entry.kind === kind)} />
      ))}
    </div>
  )
}

function MoveCard({ kind, calendars }: { kind: 'icloud' | 'google'; calendars: Movable[] }) {
  const { t } = useTranslation()
  const offline = useOffline()
  const [chosen, setChosen] = useState<number>(calendars[0].id)
  const [preview, setPreview] = useState<MovePreview | null>(null)
  const [result, setResult] = useState<MoveResult | null>(null)
  const [problem, setProblem] = useState('')
  const [moving, setMoving] = useState(false)
  const [progress, setProgress] = useState<JobState | null>(null)
  const ref = useRef(newRef())
  const running = useRef(false)
  const mounted = useMounted()
  const source = t(`move.kinds.${kind}`)
  const maxMb = useCalendars().list?.attachment_max_mb ?? 25

  useEffect(() => {
    let live = true
    setPreview(null)
    transferApi
      .movePreview(chosen)
      .then((answer) => live && setPreview(answer))
      .catch((error) => live && setProblem(problemText(error)))
    return () => {
      live = false
    }
  }, [chosen])

  const go = async () => {
    if (running.current) return
    running.current = true
    setMoving(true)
    setProblem('')
    try {
      const asked = ref.current
      const ended = await follow(await transferApi.move(chosen, asked), () => transferApi.moveStatus(asked), (state) => mounted.current && setProgress(state), () => mounted.current)
      if (!mounted.current) return
      if (ended.state === 'done' && ended.result) {
        setResult(ended.result as MoveResult)
        // Another move is another reference; this one gave its result.
        ref.current = newRef()
        announce()
      } else if (ended.state === 'failed') {
        // Nothing half (EC-9): the same reference starts the same move again.
        setProblem(errorText(ended.problem ?? 'failed'))
      }
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      running.current = false
      setMoving(false)
      setProgress(null)
    }
  }

  return (
    <Card icon={ArrowLeftRight} title={t(`move.move.title.${kind}`)} subtitle={t(`move.move.text.${kind}`)} id={`move-${kind}`}>
      {result ? (
        <div className="space-y-2" data-testid="move-result">
          <div className="flex flex-wrap items-center gap-2 text-sm text-mist-100">
            <Swatch color={calendars.find((entry) => entry.id === result.from.id)?.color ?? 'slate'} />
            <span className="nt-wrap">{t('move.move.from', { name: result.from.name, source })}</span>
            <span className="text-mist-500" aria-hidden>
              →
            </span>
            <span className="nt-wrap">{result.calendar.name}</span>
          </div>
          <Hint tone="ok" testId="move-done">
            {t('move.move.done', { count: result.made })}
            {result.failed.length > 0 && ` ${failedText(result.failed, t, maxMb)}`}
            {result.existing > 0 && ` ${t('move.load.wereThere', { count: result.existing })}`}
          </Hint>
          {result.invitations > 0 && <p className="text-xs text-mist-500" data-testid="move-invitations">{t('move.move.invitationsLeft', { count: result.invitations, source })}</p>}
          <p className="text-xs leading-relaxed text-mist-500" data-testid="move-stays">
            {t('move.move.stays', { source })}
          </p>
          {result.shared && <Hint tone="warn" testId="move-shared">{t('move.move.shared')}</Hint>}
        </div>
      ) : (
        <>
          <label className="block text-sm">
            <span className="sr-only">{t('move.move.calendar', { source })}</span>
            <select
              className="nt-field"
              value={chosen}
              onChange={(event) => {
                // Another calendar is another move: a reference names one move of one calendar.
                ref.current = newRef()
                setProblem('')
                setChosen(Number(event.target.value))
              }}
              data-testid="move-calendar"
            >
              {calendars.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {t('move.move.option', { name: entry.name, source })}
                </option>
              ))}
            </select>
          </label>
          {preview && (
            <div className="space-y-1 text-xs text-mist-500" data-testid="move-preview">
              <p>{t('move.move.count', { count: preview.events })}</p>
              {preview.invitations > 0 && <p>{t('move.move.invitationsStay', { count: preview.invitations })}</p>}
              {preview.moved_to && <p data-testid="move-again">{t('move.move.again', { name: preview.moved_to.name })}</p>}
              {preview.shared && <p>{t('move.move.sharedBefore')}</p>}
            </div>
          )}
          {preview?.over_limit && <Hint tone="warn" testId="move-limit">{t('move.limit', { count: preview.account_events + preview.events, limit: preview.limit })}</Hint>}
          <div>
            <Button kind="primary" icon="ArrowRightLeft" busy={moving} disabled={offline || !preview} onClick={() => void go()} testId="move-go">
              {moving ? t('move.move.moving') : t('move.move.go')}
            </Button>
          </div>
          {progress && <Progress state={progress} label="move.move.running" />}
        </>
      )}
      {problem && (
        <p role="alert" className="text-sm text-bad-500" data-testid="move-problem">
          {problem}
        </p>
      )}
    </Card>
  )
}
