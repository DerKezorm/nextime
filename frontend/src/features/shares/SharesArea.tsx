/**
 * Settings › My account › Shares (TIME-19 AC-6, T19-Freigaben): everything the member shares and everything shared
 * with it, in one place. "What you share": per calendar the person with right and since when, or the guest link with
 * since and until, each with "Zurücknehmen" (asked first). "Shared with you": per calendar its owner and the right, with
 * "Verlassen" (asked first), never the other people it is shared with (AC-16); a calendar that went shows a notice with
 * "Ausblenden" (AC-14). Without anything, a sentence says where sharing starts (EC-6). Cards and rows as nexnotes'
 * settings.
 */
import { Share2, Users } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { day } from '../../lib/format'
import { problemText } from '../../lib/errors'
import { Button, Card, ConfirmDialog, Notice } from '../../vendor/nexui'
import { announce } from '../calendars/store'
import { Swatch } from '../calendars/Swatch'
import { sharesApi, type MineRow, type SharesOverview, type WithMeRow } from './api'

type Asking = { kind: 'person'; row: Extract<MineRow, { kind: 'person' }> } | { kind: 'link'; row: Extract<MineRow, { kind: 'link' }> } | { kind: 'leave'; row: WithMeRow }

export function SharesArea() {
  const { t, i18n } = useTranslation()
  const [overview, setOverview] = useState<SharesOverview | null>(null)
  const [problem, setProblem] = useState('')
  const [asking, setAsking] = useState<Asking | null>(null)

  const load = useCallback(() => {
    sharesApi.overview().then(
      (answer) => setOverview({ mine: answer?.mine ?? [], with_me: answer?.with_me ?? [], notices: answer?.notices ?? [] }),
      (error) => setProblem(problemText(error)),
    )
  }, [])
  useEffect(load, [load])

  const dated = (value: string) => day(value, i18n.language)
  const source = (kind: string) => (kind === 'own' ? '' : ` (${t(`shares.kinds.${kind}`)})`)

  const [busy, setBusy] = useState(false)
  const [dialogProblem, setDialogProblem] = useState<string | null>(null)
  const confirm = async () => {
    if (!asking) return
    setBusy(true)
    setDialogProblem(null)
    try {
      if (asking.kind === 'person') await sharesApi.remove(asking.row.calendar.id, asking.row.person.id)
      else if (asking.kind === 'link') await sharesApi.revokeLink(asking.row.calendar.id)
      else await sharesApi.leave(asking.row.calendar.id)
    } catch (error) {
      setDialogProblem(problemText(error))
      return
    } finally {
      setBusy(false)
    }
    setAsking(null)
    announce()
    load()
  }

  const dismiss = async (id: number) => {
    try {
      await sharesApi.dismiss(id)
      load()
    } catch (error) {
      setProblem(problemText(error))
    }
  }

  if (!overview)
    return problem ? (
      <p role="alert" className="text-sm text-bad-500">
        {problem}
      </p>
    ) : null

  const nothing = overview.mine.length === 0 && overview.with_me.length === 0 && overview.notices.length === 0
  return (
    <div className="space-y-6" data-testid="shares-area">
      {nothing && (
        <p className="nt-empty" data-testid="shares-empty">
          {t('shares.area.empty')}
        </p>
      )}
      <Card id="shares-mine" icon={Share2} title={t('shares.area.mine')}>
        {overview.mine.length === 0 && <p className="text-sm text-mist-500">{t('shares.area.mineNone')}</p>}
        <ul className="flex flex-col" data-testid="shares-mine">
          {overview.mine.map((row) => (
            <li key={row.kind === 'person' ? `p-${row.calendar.id}-${row.person.id}` : `l-${row.calendar.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-ink-800 py-2.5 last:border-0" data-testid="shares-mine-row">
              <Swatch color={row.calendar.color} icon={row.calendar.icon} />
              <span className="w-40 min-w-0 truncate text-sm font-medium" title={row.calendar.name + source(row.calendar.kind)}>
                {row.calendar.name + source(row.calendar.kind)}
              </span>
              <span className="min-w-0 flex-1 truncate text-[13px] text-mist-300">
                {row.kind === 'person' ? t('shares.area.person', { name: row.person.name, right: t(`shares.right.${rightWord(row.right, row.calendar.kind)}`) }) : t('shares.area.link')}
              </span>
              <span className="text-xs text-mist-500">
                {row.kind === 'person'
                  ? t('shares.area.since', { date: dated(row.since) })
                  : row.until
                    ? t(row.active ? 'shares.area.until' : 'shares.area.ran', { date: day(`${row.until}T12:00:00`, i18n.language) })
                    : t('shares.link.untilRevoked')}
              </span>
              <Button kind="danger" size="sm" icon="Undo2" onClick={() => setAsking(row.kind === 'person' ? { kind: 'person', row } : { kind: 'link', row })} testId="shares-take-back">
                {t('shares.area.takeBack')}
              </Button>
            </li>
          ))}
        </ul>
      </Card>
      <Card id="shares-with-me" icon={Users} title={t('shares.area.withMe')}>
        {overview.with_me.length === 0 && overview.notices.length === 0 && <p className="text-sm text-mist-500">{t('shares.area.withMeNone')}</p>}
        <ul className="flex flex-col" data-testid="shares-with-me">
          {overview.with_me.map((row) => (
            <li key={row.calendar.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-ink-800 py-2.5 last:border-0" data-testid="shares-with-me-row">
              <Swatch color={row.calendar.color} icon={row.calendar.icon} />
              <span className="w-40 min-w-0 truncate text-sm font-medium" title={row.calendar.name}>
                {row.calendar.name}
              </span>
              <span className="min-w-0 flex-1 truncate text-[13px] text-mist-300">
                {t('shares.area.from', { name: row.owner, right: t(`shares.right.${rightWord(row.right, row.calendar.kind)}`) })}
              </span>
              <Button kind="danger" size="sm" icon="LogOut" onClick={() => setAsking({ kind: 'leave', row })} testId="shares-leave">
                {t('shares.area.leave')}
              </Button>
            </li>
          ))}
          {overview.notices.map((notice) => (
            <li key={`n-${notice.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-ink-800 py-2.5 last:border-0" data-testid="shares-notice">
              <span className="min-w-0 flex-1 text-[13px] text-mist-400">{t('shares.area.gone', { calendar: notice.calendar, name: notice.owner })}</span>
              <Button kind="secondary" size="sm" icon="Check" onClick={() => void dismiss(notice.id)} testId="shares-hide">
                {t('shares.area.hide')}
              </Button>
            </li>
          ))}
        </ul>
      </Card>
      {problem && <Notice tone="danger">{problem}</Notice>}
      {asking && (
        <ConfirmDialog
          title={
            asking.kind === 'person'
              ? t('shares.area.askPerson', { name: asking.row.person.name })
              : asking.kind === 'link'
                ? t('shares.area.askLink')
                : t('shares.area.askLeave', { calendar: asking.row.calendar.name })
          }
          text={
            asking.kind === 'person'
              ? t('shares.area.askPersonText', { name: asking.row.person.name, calendar: asking.row.calendar.name })
              : asking.kind === 'link'
                ? t('shares.area.askLinkText', { calendar: asking.row.calendar.name })
                : t('shares.area.askLeaveText', { name: asking.row.owner })
          }
          confirm={asking.kind === 'leave' ? { label: t('shares.area.leave'), kind: 'danger', icon: 'LogOut' } : { label: t('shares.area.takeBack'), kind: 'danger', icon: 'Undo2' }}
          cancel={t('common.cancel')}
          closeLabel={t('common.close')}
          busy={busy}
          error={dialogProblem}
          onCancel={() => (setAsking(null), setDialogProblem(null))}
          onConfirm={() => void confirm()}
        />
      )}
    </div>
  )
}

/** The word for a right: "lesen"/"bearbeiten" for own calendars, "ganz"/"nur belegt" for those from a source. */
function rightWord(right: string, kind: string): string {
  if (right === 'busy') return 'busy'
  if (right === 'read' && kind !== 'own') return 'whole'
  return right
}
