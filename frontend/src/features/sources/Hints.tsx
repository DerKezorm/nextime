/**
 * The hints over the views (Heute, Tag, Woche, Monat): a source that does not answer, with the calendars that show an
 * older state and "Erneut versuchen" (TIME-15 EC-5), gone by itself once it answers again; and a change a source
 * refused for good, with "Als neuen Termin anlegen" and "Verwerfen" (EC-6, TIME-5 EC-5).
 */
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { problemText } from '../../lib/errors'
import { showNotice } from '../../lib/notice'
import { useDisplay } from '../../lib/useDisplay'
import { clockText, dayName } from '../calendars/time'
import { sourcesApi, type Notice } from './api'
import { useKindName, useNames, useSources, useStamp } from './store'

export function SourceHints() {
  const { t } = useTranslation()
  const { data, take } = useSources()
  const kindName = useKindName()
  const names = useNames()
  const stamp = useStamp()
  const [problem, setProblem] = useState<string | null>(null)
  const running = useRef(new Set<string>())
  if (!data) return null
  const away = data.sources.filter((row) => row.state === 'unreachable' && row.calendars.length > 0 && row.kind !== 'subscription')
  if (!away.length && !data.notices.length && !problem) return null

  const once = async (key: string, work: () => Promise<void>) => {
    if (running.current.has(key)) return
    running.current.add(key)
    setProblem(null)
    try {
      await work()
    } catch (error) {
      setProblem(problemText(error))
    } finally {
      running.current.delete(key)
    }
  }

  return (
    <div className="space-y-2 px-3 pt-3 sm:px-6" data-testid="source-hints">
      {away.map((row) => (
        <div key={row.id} role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[9px] bg-warn-wash px-3 py-2 text-sm text-warn-500" data-testid="unreachable-hint">
          <span className="min-w-0 flex-[1_1_220px]">
            {t('sources.unreachableHint', { kind: kindName(row.kind), names: names(row.calendars.map((entry) => entry.name)), when: stamp(row.synced_at), count: row.calendars.length })}
          </span>
          <button type="button" className="nt-btn nt-btn-quiet text-sm text-warn-500 underline" onClick={() => void once(`refresh-${row.id}`, async () => take(await sourcesApi.refresh(row.id)))} data-testid="unreachable-retry">
            {t('sources.retry')}
          </button>
        </div>
      ))}
      {data.notices.map((notice) => (
        <div key={notice.id} role="status" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[9px] bg-[var(--color-bad-wash)] px-3 py-2 text-sm text-bad-500" data-testid="notice">
          <span className="nt-wrap min-w-0 flex-[1_1_220px]" data-testid="notice-text">
            <NoticeText notice={notice} />
          </span>
          <span className="flex flex-wrap gap-2">
            {notice.lost && (
              <button
                type="button"
                className="nt-btn nt-btn-ghost h-9 px-3"
                onClick={() =>
                  void once(`notice-${notice.id}`, async () => {
                    take(await sourcesApi.settle(notice.id, true))
                    showNotice({ text: t('sources.notice.created', { title: notice.title }) })
                  })
                }
                data-testid="notice-create"
              >
                {t('sources.notice.create')}
              </button>
            )}
            <button type="button" className="nt-btn nt-btn-ghost h-9 px-3" onClick={() => void once(`notice-${notice.id}`, async () => take(await sourcesApi.settle(notice.id, false)))} data-testid="notice-discard">
              {t('sources.notice.discard')}
            </button>
          </span>
        </div>
      ))}
      {problem && (
        <p role="alert" className="text-sm text-bad-500">
          {problem}
        </p>
      )}
    </div>
  )
}

/** "iCloud hat deine Änderung an „Werkstatt“ nicht übernommen (Fr., 27.11. · 09:00–09:45). Der Termin zeigt wieder
 * den Stand aus iCloud." */
function NoticeText({ notice }: { notice: Notice }) {
  const { t, i18n } = useTranslation()
  const { twelve } = useDisplay()
  const kindName = useKindName()
  const source = kindName(notice.source)
  if (!notice.lost) return <>{t('sources.notice.deleted', { source, title: notice.title })}</>
  const lost = notice.lost
  const day = dayName(lost.start.slice(0, 10), i18n.language)
  const what = lost.all_day ? day : `${day} · ${clockText(lost.start.slice(11, 16), i18n.language, twelve)}–${clockText(lost.end.slice(11, 16), i18n.language, twelve)}`
  return <>{t('sources.notice.changed', { source, title: notice.title, what })}</>
}
