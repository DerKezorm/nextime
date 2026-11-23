/**
 * The row "Erinnerungen" of an event (TIME-7 AC-2, T5-Termin): one chip per reminder with ×, then "+ Erinnerung" with
 * the fixed choices (5, 15, 30 minutes, 1 hour, 1 day; at a whole day "am Vortag um 18:00", "am Tag um 09:00") and
 * "Frei wählen …" (a number and a unit; empty, negative, letters are refused with a sentence, EC-8). The same reminder
 * twice is there once. The reminders are the member's own (AC-9).
 */
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '../../components/Dialog'
import { Menu, MenuLine } from '../../components/Menu'
import { useDisplay } from '../../lib/useDisplay'
import { leadLabel } from '../calendars/labels'
import { MAX_MINUTES, sortLeads } from './leads'

export function ReminderChips({
  minutes,
  presets,
  allDay,
  disabled = false,
  onChange,
  note,
  label: title,
}: {
  minutes: number[]
  presets: number[]
  allDay: boolean
  disabled?: boolean
  onChange: (minutes: number[]) => void
  note?: string
  /** Another name than "Erinnerungen" (the two fields of the defaults). */
  label?: string
}) {
  const { t } = useTranslation()
  const { twelve, language } = useDisplay()
  const [menu, setMenu] = useState<HTMLElement | null>(null)
  const [free, setFree] = useState(false)
  const labelId = useId()
  const label = (value: number) => leadLabel(value, allDay, t, language, twelve)
  const add = (value: number) => onChange(sortLeads([...minutes, value]))
  const open = presets.filter((value) => !minutes.includes(value))
  return (
    <div className="text-sm" data-testid="reminders">
      <span id={labelId} className="font-medium text-mist-100">
        {title ?? t('panel.reminders')}
      </span>
      <div role="group" aria-labelledby={labelId} className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {minutes.map((value) => (
          <button
            key={value}
            type="button"
            disabled={disabled}
            aria-label={t('reminders.remove', { label: label(value) })}
            className="nt-chip"
            onClick={() => onChange(minutes.filter((each) => each !== value))}
            data-testid="reminder-chip"
          >
            {label(value)}
            <span aria-hidden className="text-mist-500">
              ×
            </span>
          </button>
        ))}
        <button
          type="button"
          disabled={disabled}
          aria-haspopup="menu"
          aria-expanded={menu !== null}
          className={'nt-chip border-dashed text-accent-400 ' + (menu ? 'bg-accent-wash' : '')}
          onClick={(event) => setMenu(menu ? null : event.currentTarget)}
          data-testid="reminder-add"
        >
          {t('reminders.add')}
        </button>
      </div>
      {note && <p className="mt-1 text-xs text-mist-500">{note}</p>}
      {menu && (
        <Menu anchor={menu} onClose={() => setMenu(null)} label={t('reminders.menu')} align="start" testId="reminder-menu">
          {open.map((value) => (
            <button key={value} type="button" role="menuitem" className="nt-menu-item" onClick={() => (add(value), setMenu(null))} data-testid="reminder-option">
              {label(value)}
            </button>
          ))}
          {open.length > 0 && <MenuLine />}
          <button type="button" role="menuitem" className="nt-menu-item" onClick={() => (setFree(true), setMenu(null))} data-testid="reminder-free">
            {t('reminders.free')}
          </button>
        </Menu>
      )}
      {free && (
        <FreeReminder
          onTake={(value) => {
            // At a whole day the minutes count back from its midnight: "2 hours before" is 22:00 the day before.
            add(value)
            setFree(false)
          }}
          onClose={() => setFree(false)}
        />
      )}
    </div>
  )
}

/** "Frei wählen …": how long before, in minutes, hours or days; a whole number above 0, at most four weeks (EC-8). */
export function FreeReminder({ onTake, onClose }: { onTake: (minutes: number) => void; onClose: () => void }) {
  const { t } = useTranslation()
  const [amount, setAmount] = useState('2')
  const [unit, setUnit] = useState(60)
  const [problem, setProblem] = useState('')
  const ids = { amount: useId(), unit: useId(), problem: useId() }
  const take = () => {
    const text = amount.trim()
    if (!/^\d+$/.test(text) || Number(text) <= 0) return setProblem(t('reminders.freeWrong'))
    const minutes = Number(text) * unit
    if (minutes > MAX_MINUTES) return setProblem(t('reminders.freeTooFar'))
    onTake(minutes)
  }
  return (
    <Dialog title={t('reminders.freeTitle')} onClose={onClose}>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          take()
        }}
        data-testid="reminder-free-dialog"
      >
        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm" htmlFor={ids.amount}>
            <span className="font-medium text-mist-100">{t('reminders.howLong')}</span>
            <input
              id={ids.amount}
              inputMode="numeric"
              value={amount}
              onChange={(event) => (setAmount(event.target.value), setProblem(''))}
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? ids.problem : undefined}
              className={'nt-field mt-1 ' + (problem ? 'border-bad-500' : '')}
              data-testid="reminder-free-amount"
            />
          </label>
          <label className="block text-sm" htmlFor={ids.unit}>
            <span className="font-medium text-mist-100">{t('reminders.unit')}</span>
            <select id={ids.unit} value={unit} onChange={(event) => setUnit(Number(event.target.value))} className="nt-field mt-1" data-testid="reminder-free-unit">
              <option value={1}>{t('reminders.minutes')}</option>
              <option value={60}>{t('reminders.hours')}</option>
              <option value={1440}>{t('reminders.days')}</option>
            </select>
          </label>
        </div>
        {problem && (
          <p id={ids.problem} role="alert" className="text-xs text-bad-500" data-testid="reminder-free-problem">
            {problem}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button type="submit" className="nt-btn nt-btn-accent" data-testid="reminder-free-take">
            {t('reminders.take')}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
