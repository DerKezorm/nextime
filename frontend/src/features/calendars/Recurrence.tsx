/**
 * The dialog "Repeat · <title>" (TIME-6 AC-5): every n days/weeks/months/years, the weekdays under "On", "Ends", the
 * sentence to check it by that follows every change (AC-4) and wraps when long (EC-13), the checks at their field
 * (EC-8). And the question of a series: only this one, this and the following, or all (AC-2).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '../../components/Dialog'
import type { Rule, Scope, Weekday } from './api'
import { isLastOf, monthlyWhat, nthOf, ruleProblems, ruleSentence, WEEKDAYS, weekdayName, type RuleProblems, type Times } from './rule'
import { addDays } from './time'

type Draft = Omit<Rule, 'interval' | 'count'> & { interval: string; count: string }

export function RecurrenceDialog({
  title,
  first,
  times,
  rule,
  twelve,
  onApply,
  onClose,
}: {
  title: string
  first: string
  times: Times
  rule: Rule
  twelve: boolean
  onApply: (rule: Rule | null) => void
  onClose: () => void
}) {
  const { t, i18n } = useTranslation()
  const [draft, setDraft] = useState<Draft>({ ...rule, interval: String(rule.interval), count: String(rule.count ?? 10) })
  const [problems, setProblems] = useState<RuleProblems>({})
  const asRule = (value: Draft): Rule => ({
    ...value,
    interval: Number(value.interval) || 1,
    count: value.end === 'count' ? Number(value.count) || 0 : null,
    until: value.end === 'until' ? value.until : null,
  })
  const set = (change: Partial<Draft>) => setDraft((value) => ({ ...value, ...change }))
  const unit = { daily: 'days', weekly: 'weeks', monthly: 'months', yearly: 'years' } as const
  const nth = nthOf(first)
  const apply = () => {
    const found = ruleProblems(draft, first, t)
    setProblems(found)
    if (Object.keys(found).length) return
    onApply(asRule(draft))
  }
  return (
    <Dialog title={t('rule.dialogTitle', { title: title.trim() || t('panel.newTitle') })} onClose={onClose} medium>
      <div className="space-y-4 text-sm" data-testid="rule-dialog">
        <div className="flex flex-wrap items-center gap-2">
          <span>{t('rule.every')}</span>
          <input
            value={draft.interval}
            onChange={(event) => set({ interval: event.target.value })}
            inputMode="numeric"
            aria-label={t('rule.everyHowMany')}
            aria-invalid={problems.interval ? true : undefined}
            className={'nt-field w-16 text-center ' + (problems.interval ? 'border-bad-500' : '')}
            data-testid="rule-interval"
          />
          <select value={draft.freq} onChange={(event) => set({ freq: event.target.value as Rule['freq'], weekdays: draft.weekdays.length ? draft.weekdays : [WEEKDAYS[(new Date(`${first}T00:00:00Z`).getUTCDay() + 6) % 7]] })} aria-label={t('rule.unit')} className="nt-field w-auto" data-testid="rule-freq">
            {(['daily', 'weekly', 'monthly', 'yearly'] as const).map((freq) => (
              <option key={freq} value={freq}>
                {t(`rule.units.${unit[freq]}`)}
              </option>
            ))}
          </select>
        </div>
        {problems.interval && <Problem text={problems.interval} />}
        {draft.freq === 'weekly' && (
          <div>
            <span className="nt-legend block">{t('rule.on')}</span>
            <div role="group" aria-label={t('rule.weekdays')} className="flex flex-wrap gap-1.5">
              {WEEKDAYS.map((day: Weekday) => (
                <button
                  key={day}
                  type="button"
                  aria-pressed={draft.weekdays.includes(day)}
                  aria-label={weekdayName(day, i18n.language)}
                  onClick={() => set({ weekdays: draft.weekdays.includes(day) ? draft.weekdays.filter((item) => item !== day) : [...draft.weekdays, day] })}
                  className="nt-chip min-w-[34px] justify-center px-2.5"
                  data-testid={`rule-day-${day}`}
                >
                  {weekdayName(day, i18n.language, 'short')}
                </button>
              ))}
            </div>
            {problems.weekdays && <Problem text={problems.weekdays} />}
          </div>
        )}
        {draft.freq === 'monthly' && (
          <fieldset className="space-y-1.5">
            <legend className="nt-legend">{t('rule.inMonth')}</legend>
            <Radio name="monthly" checked={draft.monthly === 'date'} onChange={() => set({ monthly: 'date' })} label={t('rule.onWhat', { what: monthlyWhat('date', first, t, i18n.language) })} />
            {nth < 5 && <Radio name="monthly" checked={draft.monthly === 'nth'} onChange={() => set({ monthly: 'nth' })} label={t('rule.onWhat', { what: monthlyWhat('nth', first, t, i18n.language) })} testId="rule-nth" />}
            {isLastOf(first) && (
              <Radio name="monthly" checked={draft.monthly === 'last' || (draft.monthly === 'nth' && nth === 5)} onChange={() => set({ monthly: 'last' })} label={t('rule.onWhat', { what: monthlyWhat('last', first, t, i18n.language) })} testId="rule-last" />
            )}
          </fieldset>
        )}
        <fieldset className="space-y-1.5">
          <legend className="nt-legend">{t('rule.ends')}</legend>
          <Radio name="end" checked={draft.end === 'never'} onChange={() => set({ end: 'never' })} label={t('rule.never')} />
          <div className="flex flex-wrap items-center gap-2">
            <Radio name="end" checked={draft.end === 'until'} onChange={() => set({ end: 'until', until: draft.until ?? addDays(first, 90) })} label={t('rule.onDate')} testId="rule-end-until" />
            {draft.end === 'until' && (
              <input type="date" value={draft.until ?? ''} onChange={(event) => set({ until: event.target.value })} aria-label={t('rule.endDate')} className={'nt-field nt-date w-auto ' + (problems.until ? 'border-bad-500' : '')} data-testid="rule-until" />
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Radio name="end" checked={draft.end === 'count'} onChange={() => set({ end: 'count' })} label={t('rule.after')} testId="rule-end-count" />
            {draft.end === 'count' && (
              <>
                <input value={draft.count} onChange={(event) => set({ count: event.target.value })} inputMode="numeric" aria-label={t('rule.howMany')} className={'nt-field w-20 text-center ' + (problems.count ? 'border-bad-500' : '')} data-testid="rule-count" />
                <span>{t('rule.occurrences')}</span>
              </>
            )}
          </div>
          {problems.until && <Problem text={problems.until} />}
          {problems.count && <Problem text={problems.count} />}
        </fieldset>
        <p aria-live="polite" className="rounded-[9px] bg-[var(--color-accent-wash)] px-3 py-2 break-words text-mist-100" data-testid="rule-sentence">
          {ruleSentence(asRule(draft), first, times, t, i18n.language, twelve)}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="nt-btn nt-btn-quiet mr-auto" onClick={() => onApply(null)} data-testid="rule-none">
            {t('rule.none')}
          </button>
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="button" className="nt-btn nt-btn-accent" onClick={apply} data-testid="rule-apply">
            {t('rule.apply')}
          </button>
        </div>
      </div>
    </Dialog>
  )
}

function Problem({ text }: { text: string }) {
  return (
    <span role="alert" className="block text-xs text-bad-500">
      {text}
    </span>
  )
}

function Radio({ name, checked, onChange, label, testId }: { name: string; checked: boolean; onChange: () => void; label: string; testId?: string }) {
  return (
    <label className="flex items-center gap-2">
      <input type="radio" name={name} checked={checked} onChange={onChange} className="accent-[var(--color-accent-500)]" data-testid={testId} />
      <span>{label}</span>
    </label>
  )
}

/** "„Training“ auf 19:00 verschieben?" with "Der Termin am Do, 26. November gehört zu einer Serie." (AC-2). */
export function SeriesQuestion({ question, day, single, onChoose, onClose }: { question: string; day: string; single: boolean; onChoose: (scope: Scope) => void; onClose: () => void }) {
  const { t } = useTranslation()
  return (
    <Dialog title={question} onClose={onClose}>
      <div className="flex flex-col gap-2" data-testid="series-question">
        <p className="text-sm text-mist-200">{t('series.text', { day })}</p>
        {single && (
          <button type="button" className="nt-btn nt-btn-ghost justify-start" onClick={() => onChoose('this')}>
            {t('series.this')}
          </button>
        )}
        <button type="button" className="nt-btn nt-btn-ghost justify-start" onClick={() => onChoose('following')}>
          {t('series.following')}
        </button>
        <button type="button" className="nt-btn nt-btn-ghost justify-start" onClick={() => onChoose('all')}>
          {t('series.all')}
        </button>
        <button type="button" className="nt-btn nt-btn-quiet self-end" onClick={onClose}>
          {t('common.cancel')}
        </button>
      </div>
    </Dialog>
  )
}
