/**
 * The group "Aus nexpaper" in the sidebar, built like "Aus nextasks": one row "Meine Fristen" with the number due today
 * and overdue, the eye that hides the deadlines and "…" with "Nur diese zeigen", "nexpaper öffnen" and the connection.
 * A click shows the deadlines alone in the month with its column, the same again brings back the choice before. Not
 * connected, the group is not there.
 */
import { ExternalLink, Eye, EyeOff, FileClock, MoreHorizontal, SlidersHorizontal, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { Menu, MenuEntry, type MenuAnchor } from '../../components/Menu'
import { problemText } from '../../lib/errors'
import { useDisplay } from '../../lib/useDisplay'
import { useOnly } from '../collections/store'
import { useOffline } from '../offline/connection'
import { todayAt, useNow } from '../views/state'
import { DEADLINES_ROW, nexpaperApi, setNexpaper } from './api'
import { useDeadlines, useNexpaper } from './hooks'

export function NexpaperGroup({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation()
  const status = useNexpaper()
  const offline = useOffline()
  const { zone } = useDisplay()
  const today = todayAt(useNow(), zone)
  const { deadlines } = useDeadlines(today, { all: true })
  const { onlyArea, setOnlyArea } = useOnly()
  const navigate = useNavigate()
  const [menu, setMenu] = useState<MenuAnchor | null>(null)
  const [problem, setProblem] = useState('')
  if (!status?.connected) return null
  const hidden = Boolean(status.hidden)
  const alone = onlyArea === DEADLINES_ROW
  const number = (deadlines ?? []).length
  const name = t('deadlines.mine')

  const toggle = async () => {
    setProblem('')
    try {
      setNexpaper(await nexpaperApi.setHidden(!hidden))
    } catch (error) {
      setProblem(problemText(error))
    }
  }
  const showAlone = () => {
    if (alone) return setOnlyArea(null)
    setOnlyArea(DEADLINES_ROW)
    navigate('/month')
    onNavigate?.()
  }

  return (
    <div data-testid="calendar-group-nexpaper">
      <div className="mt-3 flex items-center justify-between gap-2 rounded-lg pr-1 pl-2.5">
        <h3 className="py-1 text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase">{t('deadlines.group')}</h3>
      </div>
      <ul>
        <li className={'group relative flex h-8 items-center gap-1.5 rounded-lg pr-1 text-[13.5px] hover:bg-ink-800 pointer-coarse:h-11 ' + (alone ? 'bg-ink-800' : '')} data-testid="deadlines-row">
          <button type="button" className="flex h-full min-w-0 flex-1 items-center gap-2.5 pl-2.5 text-left select-none" onClick={showAlone} data-testid="deadlines-row-name">
            <FileClock className={'h-[14px] w-[14px] shrink-0 ' + (hidden ? 'text-mist-600' : 'text-mist-400')} aria-hidden />
            <span className={'min-w-0 flex-1 truncate ' + (hidden ? 'text-mist-500' : 'text-mist-100')}>{name}</span>
            {!hidden && number > 0 && (
              <span className="shrink-0 text-xs text-mist-500 group-focus-within:hidden group-hover:hidden pointer-coarse:inline" title={t('deadlines.countTitle', { count: number })} data-testid="deadlines-count">
                {number}
              </span>
            )}
          </button>
          <button
            type="button"
            aria-label={hidden ? t('deadlines.show') : t('deadlines.hide')}
            aria-pressed={!hidden}
            disabled={offline}
            className={
              'h-6 w-6 shrink-0 items-center justify-center rounded p-0.5 text-mist-400 hover:bg-ink-700 hover:text-mist-100 disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:flex ' +
              (hidden ? 'flex' : 'hidden group-focus-within:flex group-hover:flex')
            }
            onClick={() => void toggle()}
            data-testid="deadlines-eye"
          >
            {hidden ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
          </button>
          <button
            type="button"
            aria-label={t('deadlines.menu')}
            className="hidden h-6 w-6 shrink-0 items-center justify-center rounded p-0.5 text-mist-400 group-focus-within:flex group-hover:flex hover:bg-ink-700 hover:text-mist-100 pointer-coarse:flex"
            onClick={(event) => setMenu(event.currentTarget)}
            data-testid="deadlines-more-menu"
          >
            <MoreHorizontal className="h-4 w-4" aria-hidden />
          </button>
        </li>
      </ul>
      {menu && (
        <Menu anchor={menu} onClose={() => setMenu(null)} label={t('deadlines.menu')} testId="deadlines-menu">
          <MenuEntry icon={alone ? Undo2 : Eye} onClick={() => (showAlone(), setMenu(null))} testId="deadlines-only">
            {alone ? t('collections.previous') : t('collections.onlyThis')}
          </MenuEntry>
          {status.url && (
            <MenuEntry
              icon={ExternalLink}
              onClick={() => {
                const opened = window.open(status.url, '_blank', 'noopener')
                if (opened) opened.opener = null
                setMenu(null)
              }}
              testId="deadlines-open"
            >
              {t('deadlines.openNexpaper')}
            </MenuEntry>
          )}
          <MenuEntry icon={SlidersHorizontal} onClick={() => (navigate('/settings/connections#nexpaper'), setMenu(null), onNavigate?.())} testId="deadlines-connection">
            {t('deadlines.connection')}
          </MenuEntry>
        </Menu>
      )}
      {problem && (
        <p role="alert" className="px-2.5 text-xs text-bad-500">
          {problem}
        </p>
      )}
    </div>
  )
}
