/**
 * Server › Log (TIME-1 AC-9), laid out as in the other nex apps: a table of time, level, source, request and
 * account, message; chips for the level (a level shows the higher ones too), a search in the text, download and clear
 * (asks first), the level of detail beside them. On a phone each line is a card, no table and no sideways scrolling.
 * The newest come first; older ones page by page.
 */
import { Download, Info, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { clock, moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { Button, Card, Confirm, Feedback, useAction } from './ui'

/** `at`: the moment with its offset, shown in the reader's own time; `time` is the server's clock. */
type LogLine = { time: string; level: string; logger: string; message: string; request_id?: string | null; user?: string | null; at?: string | null }
type LogMode = { mode: string; until: string | null; fixed_by_env: boolean; modes: string[] }

const LOG_LEVELS = ['', 'INFO', 'WARNING', 'ERROR'] as const
/** Lines per page. */
const LOG_PAGE = 200

const TONE: Record<string, string> = { DEBUG: 'text-mist-600', INFO: 'text-mist-500', WARNING: 'text-warn-500 font-semibold', ERROR: 'text-bad-500 font-semibold', CRITICAL: 'text-bad-500 font-semibold' }

export function LogCard() {
  const { t, i18n } = useTranslation()
  const { timeOptions } = useDisplay()
  const [lines, setLines] = useState<LogLine[] | null>(null)
  const [more, setMore] = useState(false)
  const [level, setLevel] = useState('')
  const [words, setWords] = useState('')
  const [mode, setMode] = useState<LogMode | null>(null)
  const [clearing, setClearing] = useState(false)
  const action = useAction()

  const page = useCallback(
    (offset: number) => api<LogLine[]>('/api/logs', { query: { level: level || undefined, search: words.trim() || undefined, limit: LOG_PAGE, offset } }),
    [level, words],
  )
  const load = useCallback(() => {
    page(0).then((found) => {
      setLines(found)
      setMore(found.length === LOG_PAGE)
    }, () => undefined)
  }, [page])
  useEffect(() => {
    const timer = window.setTimeout(load, 250)
    return () => window.clearTimeout(timer)
  }, [load])
  useEffect(() => {
    api<LogMode>('/api/logs/level').then(setMode, () => undefined)
  }, [])

  const older = () =>
    void page(lines?.length ?? 0).then((found) => {
      setLines([...(lines ?? []), ...found])
      setMore(found.length === LOG_PAGE)
    }, () => undefined)
  const when = (line: LogLine) => (line.at ? moment(line.at, i18n.language, true, timeOptions) : line.time)
  const request = (line: LogLine) =>
    line.request_id ? (
      <button type="button" onClick={() => setWords(line.request_id ?? '')} title={t('server.logRequestHint')} className="font-mono text-accent-400 hover:underline">
        {line.request_id}
      </button>
    ) : (
      <span className="text-mist-600">–</span>
    )

  return (
    <Card id="log" icon={Info} title={t('server.log')} text={t('server.logHint')}>
      <div className="flex flex-wrap items-center gap-2" data-testid="log-bar">
        <div role="radiogroup" aria-label={t('server.level')} className="flex flex-wrap gap-1.5">
          {LOG_LEVELS.map((value) => (
            <button
              key={value || 'all'}
              type="button"
              role="radio"
              aria-checked={level === value}
              onClick={() => setLevel(value)}
              className={
                'rounded-full border px-3 py-1 text-[13px] font-medium ' +
                (level === value ? 'border-accent-500/60 bg-accent-wash text-accent-400' : 'border-ink-700 text-mist-400 hover:text-mist-100')
              }
              data-testid={`log-level-${value || 'all'}`}
            >
              {value || t('server.logAll')}
            </button>
          ))}
        </div>
        <input
          type="search"
          value={words}
          onChange={(event) => setWords(event.target.value)}
          placeholder={t('server.logSearch')}
          aria-label={t('server.logSearch')}
          className="nt-field h-9 min-w-40 flex-1 py-1"
          data-testid="log-search"
        />
        {mode && (
          <label className="flex items-center gap-2 text-[13px] text-mist-400">
            {t('server.detail')}
            <select
              className="nt-field h-9 w-auto px-2 py-1"
              value={mode.mode}
              disabled={mode.fixed_by_env}
              data-testid="log-mode"
              onChange={(e) =>
                void action.run(async () =>
                  setMode(await api<LogMode>('/api/logs/level', { method: 'PUT', body: { mode: e.target.value, minutes: e.target.value === 'detailed' || e.target.value === 'trace' ? 60 : 0 } })),
                )
              }
            >
              {mode.modes.map((value) => (
                <option key={value} value={value}>
                  {t(`server.levels.${value}`)}
                </option>
              ))}
            </select>
          </label>
        )}
        <a href="/api/logs/download" className="nt-btn nt-btn-ghost h-9">
          <Download className="h-4 w-4" strokeWidth={1.8} aria-hidden />
          {t('server.download')}
        </a>
        <Button danger onClick={() => setClearing(true)}>
          <Trash2 className="h-4 w-4" strokeWidth={1.8} aria-hidden />
          {t('server.logClear')}
        </Button>
      </div>
      <p className="-mt-1 text-xs text-mist-500">{t('server.logLevelHint')}</p>
      {mode?.until && <p className="text-xs text-mist-500">{t('server.logUntil', { time: clock(mode.until, i18n.language, timeOptions) })}</p>}
      <Feedback problem={action.problem} done={action.done} />

      {lines?.length === 0 && <p className="rounded-[12px] border border-dashed border-ink-700 px-6 py-10 text-center text-sm text-mist-500">{t('server.logEmpty')}</p>}
      {lines && lines.length > 0 && (
        <>
          <table className="hidden w-full table-fixed border-separate border-spacing-0 overflow-hidden rounded-[12px] border border-ink-700 text-left text-xs md:table" data-testid="log-table" role="log">
            <colgroup>
              <col className="w-[9.5rem]" />
              <col className="w-[5.5rem]" />
              <col className="w-[9rem]" />
              <col className="w-[9rem]" />
              <col />
            </colgroup>
            <thead className="text-mist-500">
              <tr>
                {(['logTime', 'level', 'logSource', 'logRequest', 'logMessage'] as const).map((key) => (
                  <th key={key} className="border-b border-ink-700 px-3 py-2 font-medium">
                    {t(`server.${key}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {lines.map((line, index) => (
                <tr key={index} data-testid="log-row" data-level={line.level}>
                  <td className="border-b border-ink-700/50 px-3 py-1.5 align-top whitespace-nowrap text-mist-500">{when(line)}</td>
                  <td className={'border-b border-ink-700/50 px-3 py-1.5 align-top ' + (TONE[line.level] ?? '')} data-testid="log-level">
                    {line.level}
                  </td>
                  <td className="truncate border-b border-ink-700/50 px-3 py-1.5 align-top text-mist-500" title={line.logger}>
                    {line.logger}
                  </td>
                  <td className="border-b border-ink-700/50 px-3 py-1.5 align-top">
                    <span className="flex flex-col">
                      {request(line)}
                      {line.user && <span className="truncate text-mist-500">{line.user}</span>}
                    </span>
                  </td>
                  <td className="border-b border-ink-700/50 px-3 py-1.5 align-top break-words whitespace-pre-wrap text-mist-200">{line.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <ul className="flex flex-col gap-2 md:hidden" data-testid="log-cards" role="log">
            {lines.map((line, index) => (
              <li key={index} className="rounded-[10px] border border-ink-700 px-3 py-2 text-xs" data-testid="log-card">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                  <span className={TONE[line.level] ?? ''}>{line.level}</span>
                  <span className="text-mist-500">{when(line)}</span>
                  <span className="min-w-0 truncate text-mist-500">{line.logger}</span>
                </div>
                <p className="mt-1 break-words whitespace-pre-wrap text-mist-200">{line.message}</p>
                {(line.request_id || line.user) && (
                  <div className="mt-1 flex flex-wrap gap-2">
                    {line.request_id && request(line)}
                    {line.user && <span className="text-mist-500">{line.user}</span>}
                  </div>
                )}
              </li>
            ))}
          </ul>
          {more && (
            <button type="button" className="nt-btn nt-btn-ghost self-center" onClick={older} data-testid="log-more">
              {t('server.logMore')}
            </button>
          )}
        </>
      )}
      {clearing && (
        <Confirm
          title={t('server.logClear')}
          text={t('server.logClearText')}
          confirm={t('server.logClear')}
          danger
          onCancel={() => setClearing(false)}
          onConfirm={async () => {
            await api('/api/logs', { method: 'DELETE' })
            setClearing(false)
            setLines([])
            setMore(false)
          }}
        />
      )}
    </Card>
  )
}
