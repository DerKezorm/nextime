/**
 * The block "Fristen aus nexpaper" under the events of today, beside the tasks of nextasks and built like their block:
 * each deadline of today or overdue with "heute" or "überfällig seit Mo"; the first five and "+n weitere". nexpaper
 * away: a quiet line instead of the deadlines.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { Deadline } from './api'
import { DeadlineRow, dueText } from './DeadlineItem'

/** Deadlines shown before "+n weitere". */
export const FIRST_DEADLINES = 5

/** `phone`: at phone width the deadlines stand in the list of today with the word "Frist", without a card. */
export function DeadlinesBlock({ deadlines, today, problem, phone = false }: { deadlines: Deadline[]; today: string; problem: string | null; phone?: boolean }) {
  const { t, i18n } = useTranslation()
  const [all, setAll] = useState(false)
  if (!deadlines.length && !problem) return null
  const shown = all ? deadlines : deadlines.slice(0, FIRST_DEADLINES)
  const when = (deadline: Deadline) => {
    const due = dueText(deadline, today, t, i18n.language)
    return phone ? `${t('deadlines.kind')} · ${due}` : due
  }
  return (
    <section
      className={'flex flex-col gap-2.5 ' + (phone ? 'px-1' : 'rounded-[12px] border border-ink-700 bg-ink-850 px-4 py-3')}
      aria-labelledby="deadlines-block"
      data-testid="deadlines-block"
      data-phone={phone || undefined}
    >
      <h2 id="deadlines-block" className={phone ? 'sr-only' : 'text-xs font-semibold tracking-wider text-mist-500 uppercase'}>
        {t('deadlines.block')}
      </h2>
      {problem && (
        <p className="text-xs text-mist-500" data-testid="deadlines-problem">
          {problem === 'nexpaper_unreachable' || problem === 'nexpaper_slow_down' || problem === 'nexpaper_busy' || problem === 'slow_down' ? t('deadlines.unreachable') : t('deadlines.problem')}
        </p>
      )}
      {shown.map((deadline) => (
        <DeadlineRow key={deadline.id} deadline={deadline} when={when(deadline)} late={deadline.day < today} />
      ))}
      {deadlines.length > FIRST_DEADLINES && (
        <button type="button" className="nt-btn-quiet self-start text-xs font-semibold" aria-expanded={all} onClick={() => setAll(!all)} data-testid="deadlines-more">
          {all ? t('deadlines.less') : t('deadlines.more', { count: deadlines.length - FIRST_DEADLINES })}
        </button>
      )}
    </section>
  )
}
