/**
 * The group "nextasks" in the sidebar (TIME-25 AC-7, area rows asked for while testing, 2026-11-23): one row per area
 * chosen in "Verbindungen", built like a calendar row: its mark, its name, the number due today and overdue, the eye
 * that hides it for a while and "…" with "In nextasks öffnen" and "Bereiche wählen …". A click shows the area alone in
 * the month with its column, the same again brings back the choice before. A nextasks without areas keeps the one row
 * "Meine Aufgaben". Not connected, the group is not there (AC-5).
 */
import { ExternalLink, Eye, EyeOff, ListChecks, MoreHorizontal, SlidersHorizontal, Undo2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'

import { Menu, MenuEntry, type MenuAnchor } from '../../components/Menu'
import { problemText } from '../../lib/errors'
import { useDisplay } from '../../lib/useDisplay'
import { useOnly } from '../collections/store'
import { useOffline } from '../offline/connection'
import { todayAt, useNow } from '../views/state'
import { AreaMark } from './AreaMark'
import { nextasksApi, setNextasks, setTaskAreas, taskAreas, type TaskArea } from './api'
import { useNextasks, useTaskAreas, useTasks } from './hooks'
import { afterAlone } from '../views/state'

export function NextasksGroup({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation()
  const status = useNextasks()
  const areas = useTaskAreas()
  const offline = useOffline()
  const { zone } = useDisplay()
  const today = todayAt(useNow(), zone)
  const { tasks } = useTasks(today, { all: true })
  const { onlyArea, setOnlyArea } = useOnly()
  const navigate = useNavigate()
  const location = useLocation()
  const [menu, setMenu] = useState<{ anchor: MenuAnchor; area: TaskArea } | null>(null)
  const [problem, setProblem] = useState('')
  if (!status?.connected) return null
  const count = (row: string | null) => (tasks ?? []).filter((task) => !task.done && !task.preview && task.day <= today && (row === null || task.row === row)).length

  const act = async (work: () => Promise<void>) => {
    setProblem('')
    try {
      await work()
    } catch (error) {
      setProblem(problemText(error))
    }
  }
  const toggle = (area: TaskArea) =>
    act(async () => {
      setTaskAreas(taskAreas().map((one) => (one.id === area.id ? { ...one, hidden: !area.hidden } : one)))
      try {
        await nextasksApi.hideArea(area.id, !area.hidden)
      } catch (error) {
        setTaskAreas(taskAreas().map((one) => (one.id === area.id ? { ...one, hidden: area.hidden } : one)))
        throw error
      }
    })
  const alone = (area: TaskArea) => {
    if (onlyArea === area.id) return setOnlyArea(null)
    setOnlyArea(area.id)
    const next = afterAlone(location.pathname)
    if (next) navigate(next)
    onNavigate?.()
  }

  const row = (key: string, name: string, hidden: boolean, number: number, mark: React.ReactNode, onEye: () => void, area?: TaskArea) => (
    // A list chosen alone names its area quietly behind it, or in the tooltip when there is no room.
    <li key={key} className={'group relative flex h-8 items-center gap-1.5 rounded-lg pr-1 text-[13.5px] hover:bg-ink-800 pointer-coarse:h-11 ' + (area && onlyArea === area.id ? 'bg-ink-800' : '')} data-testid="task-area" data-area={area?.id}>
      <button type="button" className="flex h-full min-w-0 flex-1 items-center gap-2.5 pl-2.5 text-left select-none" disabled={!area} onClick={() => area && alone(area)} data-testid="task-area-name">
        {mark}
        <span className={'min-w-0 flex-1 truncate ' + (hidden ? 'text-mist-500' : 'text-mist-100')} title={area?.area_name ? `${name} · ${area.area_name}` : name}>
          {name}
          {area?.area_name && <span className="text-mist-500"> · {area.area_name}</span>}
        </span>
        {!hidden && number > 0 && (
          <span className="shrink-0 text-xs text-mist-500 group-focus-within:hidden group-hover:hidden pointer-coarse:inline" title={t('tasks.countTitle', { count: number })} data-testid="my-tasks-count">
            {number}
          </span>
        )}
      </button>
      <button
        type="button"
        aria-label={hidden ? t('tasks.showArea', { name }) : t('tasks.hideArea', { name })}
        aria-pressed={!hidden}
        disabled={offline}
        className={
          'h-6 w-6 shrink-0 items-center justify-center rounded p-0.5 text-mist-400 hover:bg-ink-700 hover:text-mist-100 disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:flex ' +
          (hidden ? 'flex' : 'hidden group-focus-within:flex group-hover:flex')
        }
        onClick={onEye}
        data-testid="task-area-eye"
      >
        {hidden ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
      </button>
      {area && (
        <button
          type="button"
          aria-label={t('tasks.areaMore', { name })}
          className="hidden h-6 w-6 shrink-0 items-center justify-center rounded p-0.5 text-mist-400 group-focus-within:flex group-hover:flex hover:bg-ink-700 hover:text-mist-100 pointer-coarse:flex"
          onClick={(event) => setMenu({ anchor: event.currentTarget, area })}
          data-testid="task-area-more"
        >
          <MoreHorizontal className="h-4 w-4" aria-hidden />
        </button>
      )}
    </li>
  )

  const legacyHidden = Boolean(status.hidden)
  return (
    <div data-testid="calendar-group-nextasks">
      <div className="mt-3 flex items-center justify-between gap-2 rounded-lg pr-1 pl-2.5">
        <h3 className="py-1 text-[11px] font-semibold tracking-[0.06em] text-mist-500 uppercase">{t('tasks.group')}</h3>
      </div>
      <ul>
        {areas.length
          ? areas.map((area) => row(area.id, area.name, Boolean(area.hidden), count(area.id), <AreaMark area={area} dim={area.hidden} />, () => void toggle(area), area))
          : row(
              'mine',
              t('tasks.mine'),
              legacyHidden,
              count(null),
              <ListChecks className={'h-[14px] w-[14px] shrink-0 ' + (legacyHidden ? 'text-mist-600' : 'text-mist-400')} aria-hidden />,
              () => void act(async () => setNextasks(await nextasksApi.setHidden(!legacyHidden))),
            )}
      </ul>
      {menu && (
        <Menu anchor={menu.anchor} onClose={() => setMenu(null)} label={t('tasks.areaMore', { name: menu.area.name })} testId="task-area-menu">
          <MenuEntry icon={onlyArea === menu.area.id ? Undo2 : Eye} onClick={() => (alone(menu.area), setMenu(null))} testId="task-area-only">
            {onlyArea === menu.area.id ? t('collections.previous') : t('collections.onlyThis')}
          </MenuEntry>
          <MenuEntry
            icon={ExternalLink}
            onClick={() => {
              const target = menu.area.link || status.url
              const opened = target ? window.open(target, '_blank', 'noopener') : null
              if (opened) opened.opener = null
              setMenu(null)
            }}
            testId="task-area-open"
          >
            {t('tasks.openNextasks')}
          </MenuEntry>
          <MenuEntry icon={SlidersHorizontal} onClick={() => (navigate('/settings/connections#nextasks'), setMenu(null), onNavigate?.())} testId="task-area-choose">
            {t('tasks.chooseAreas')}
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
