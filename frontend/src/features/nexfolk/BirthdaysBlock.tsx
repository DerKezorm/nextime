/**
 * The block "Geburtstage aus nexfolk" under the events of today (draft of the smallest form, plan "Apps verbinden"
 * V1 8a): over a connection made by a click with nexfolk, the birthdays and anniversaries of the next two weeks, each
 * with "heute", "morgen" or its day, and the age where nexfolk knows the year. Built like the block of the deadlines.
 * Without a connection: nothing.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { addDays } from '../calendars/time'

export type Birthday = { day: string; name: string; kind: 'birthday' | 'anniversary'; label: string; years: number | null }
type Birthdays = { connected: boolean; birthdays: Birthday[]; problem: string | null }

/** Shown before "+n weitere". */
export const FIRST_BIRTHDAYS = 5

export function BirthdaysBlock({ today, phone = false }: { today: string; phone?: boolean }) {
  const { t, i18n } = useTranslation()
  const [found, setFound] = useState<Birthdays | null>(null)
  const [all, setAll] = useState(false)
  useEffect(() => {
    let live = true
    api<Birthdays>('/api/birthdays').then(
      (answer) => live && setFound(answer && !Array.isArray(answer) ? answer : null),
      () => live && setFound(null),
    )
    return () => {
      live = false
    }
  }, [today])
  if (!found?.connected || (!found.birthdays.length && !found.problem)) return null
  const shown = all ? found.birthdays : found.birthdays.slice(0, FIRST_BIRTHDAYS)
  const when = (day: string) =>
    day === today
      ? t('birthdays.today')
      : day === addDays(today, 1)
        ? t('birthdays.tomorrow')
        : new Date(`${day}T12:00:00Z`).toLocaleDateString(i18n.language, { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })
  return (
    <section
      className={'flex flex-col gap-2.5 ' + (phone ? 'px-1' : 'rounded-[12px] border border-ink-700 bg-ink-850 px-4 py-3')}
      aria-labelledby="birthdays-block"
      data-testid="birthdays-block"
    >
      <h2 id="birthdays-block" className={phone ? 'sr-only' : 'text-xs font-semibold tracking-wider text-mist-500 uppercase'}>
        {t('birthdays.block')}
      </h2>
      {found.problem && (
        <p className="text-xs text-mist-500" data-testid="birthdays-problem">
          {t('birthdays.problem')}
        </p>
      )}
      {shown.map((one) => (
        <p key={`${one.day}-${one.name}`} className="m-0 flex items-baseline justify-between gap-3 text-sm" data-testid="birthday">
          <span className="min-w-0 truncate">
            {one.kind === 'birthday'
              ? one.years !== null
                ? t('birthdays.birthdayYears', { name: one.name, count: one.years })
                : t('birthdays.birthday', { name: one.name })
              : t('birthdays.anniversary', { name: one.name, label: one.label || t('birthdays.anniversaryWord') })}
          </span>
          <span className="shrink-0 text-xs text-mist-500">{when(one.day)}</span>
        </p>
      ))}
      {found.birthdays.length > FIRST_BIRTHDAYS && (
        <button type="button" className="nt-btn-quiet self-start text-xs font-semibold" aria-expanded={all} onClick={() => setAll(!all)} data-testid="birthdays-more">
          {all ? t('birthdays.less') : t('birthdays.more', { count: found.birthdays.length - FIRST_BIRTHDAYS })}
        </button>
      )}
    </section>
  )
}
