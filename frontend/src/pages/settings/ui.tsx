/**
 * Building blocks of the settings, account and about pages, the same as nexlore's (components/settings/ui.tsx and
 * components/TabRow.tsx): whoever knows one app finds the same card, row, field and tab in the other.
 */
import { ChevronDown, type LucideIcon } from 'lucide-react'
import { useCallback, useId, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { Dialog } from '../../components/Dialog'
import { errorText, problemText } from '../../lib/errors'
import { copyText } from '../../lib/copy'
import { useDevicePref } from '../../lib/devicePref'
import { go, startAddress } from '../../lib/providers'

/** A card with its symbol in a small box, a title and a line of explanation. */
/** A card of the settings; `logo` (an app's mark, as nextasks') takes the place of the symbol on its tinted tile. */
/** `foldable` names a choice of this device: the card folds to its head with the arrow on the right (the connections
 * to the other apps, so the page stays short); folded until opened, its head then shows `summary`. */
export function Card({ icon: Icon, logo, title, text, children, id, foldable, summary }: { icon: LucideIcon; logo?: string; title: string; text?: string; children: ReactNode; id?: string; foldable?: string; summary?: ReactNode }) {
  const [folded, setFolded] = useDevicePref(foldable ? `fold.${foldable}` : 'fold.none', (raw) => Boolean(foldable) && raw !== '0')
  const bodyId = useId()
  const mark = logo ? (
    <img src={logo} alt="" className="h-9 w-9 shrink-0 rounded-xl" data-testid="card-logo" />
  ) : (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--color-accent-wash)] text-accent-400">
      <Icon className="h-4 w-4" strokeWidth={1.8} aria-hidden />
    </span>
  )
  const words = (
    <div className="min-w-0 flex-1">
      <h2 id={id ? `${id}-title` : undefined} className="font-semibold text-mist-100">
        {title}
      </h2>
      {text && <p className="text-sm text-mist-500">{text}</p>}
      {folded && summary && <div className="mt-1.5" data-testid="card-summary">{summary}</div>}
    </div>
  )
  return (
    <section id={id} aria-labelledby={id ? `${id}-title` : undefined} className="rounded-[12px] border border-ink-700 bg-ink-850 p-5" data-folded={foldable ? folded : undefined}>
      {foldable ? (
        <button
          type="button"
          className={'flex w-full gap-3 text-left ' + (folded ? '' : 'mb-4')}
          aria-expanded={!folded}
          aria-controls={bodyId}
          onClick={() => setFolded(folded ? '0' : null)}
          data-testid="card-fold"
        >
          {mark}
          {words}
          <ChevronDown className={'mt-2 h-4 w-4 shrink-0 text-mist-500 transition-transform ' + (folded ? '-rotate-90' : '')} aria-hidden />
        </button>
      ) : (
        <div className="mb-4 flex gap-3">
          {mark}
          {words}
        </div>
      )}
      {!folded && (
        <div id={bodyId} className="space-y-3">
          {children}
        </div>
      )}
    </section>
  )
}

/** A heading inside a card, for a second part of it (as nexlore's "OpenID Connect" under "Sign-in"). */
export function SubHead({ title, text }: { title: string; text?: string }) {
  return (
    <div className="pt-3">
      <h3 className="text-sm font-semibold text-mist-100">{title}</h3>
      {text && <p className="text-xs text-mist-500">{text}</p>}
    </div>
  )
}

/** A switch with its name and a line of explanation, as in nexnotes: the whole row is the label (a checkbox in the
 * accent); a `note` stands behind the name ("(not set up)"). */
export function Toggle({ label, hint, checked, onChange, disabled = false, note }: { label: string; hint?: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean; note?: string }) {
  const hintId = useId()
  return (
    <label className="flex items-center justify-between gap-4 rounded-[12px] border border-ink-700 bg-ink-900 px-4 py-3 text-sm">
      <span className="min-w-0">
        <span className="font-medium text-mist-100">{label}</span>
        {note && <span className="ml-1.5 text-xs text-mist-500">{note}</span>}
        {hint && (
          <span id={hintId} className="block text-xs text-mist-500">
            {hint}
          </span>
        )}
      </span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        aria-describedby={hint ? hintId : undefined}
        onChange={(event) => onChange(event.target.checked)}
        className="h-5 w-5 shrink-0 accent-[var(--color-accent-500)]"
      />
    </label>
  )
}

/** A field with its name above it and an optional line below. */
export function Input({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
  hint,
  className = '',
  autoComplete = 'off',
  min,
  max,
  list,
  maxLength,
  error,
  inputMode,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  placeholder?: string
  hint?: string
  className?: string
  autoComplete?: string
  min?: number
  max?: number
  /** The id of a datalist with suggestions. */
  list?: string
  maxLength?: number
  /** What is wrong with the value, said at the field (TIME-1 EC-11). */
  error?: string | null
  inputMode?: 'numeric' | 'text'
}) {
  const hintId = useId()
  const errorId = useId()
  return (
    <div className={'text-sm ' + className}>
      <label className="block">
        <span className="text-xs font-medium text-mist-400">{label}</span>
        <input
          type={type}
          value={value}
          placeholder={placeholder}
          autoComplete={autoComplete}
          min={min}
          max={max}
          list={list}
          maxLength={maxLength}
          inputMode={inputMode}
          aria-invalid={error ? true : undefined}
          aria-describedby={[error ? errorId : '', hint ? hintId : ''].filter(Boolean).join(' ') || undefined}
          onChange={(event) => onChange(event.target.value)}
          className={'nt-field mt-1' + (error ? ' border-bad-500' : '')}
        />
      </label>
      {error && (
        <span id={errorId} role="alert" className="mt-1 block text-xs text-bad-500">
          {error}
        </span>
      )}
      {hint && (
        <span id={hintId} className="mt-1 block text-xs text-mist-500">
          {hint}
        </span>
      )}
    </div>
  )
}

export function Select<T extends string>({ label, value, options, onChange, className = '' }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (value: T) => void; className?: string }) {
  return (
    <label className={'block text-sm ' + className}>
      <span className="text-xs font-medium text-mist-400">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value as T)} className="nt-field mt-1 px-2">
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export function Button({
  children,
  onClick,
  busy = false,
  accent = false,
  danger = false,
  small = false,
  type = 'button',
  disabled = false,
  label,
}: {
  children: ReactNode
  onClick?: () => void
  busy?: boolean
  accent?: boolean
  danger?: boolean
  small?: boolean
  type?: 'button' | 'submit'
  disabled?: boolean
  label?: string
}) {
  const look = accent ? 'nt-btn-accent' : danger ? 'nt-btn-ghost hover:border-bad-500/50 hover:text-bad-500' : 'nt-btn-ghost'
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={busy || disabled}
      aria-label={label}
      className={`nt-btn ${small ? 'h-8 px-2.5 text-xs' : ''} ${look}`}
    >
      {children}
    </button>
  )
}

/** What went wrong: a code, or the server's refusal with the values its sentence needs ("({{max_mb}} MB)"). */
export type Trouble = string | ApiError | null

/** One line under a card: what went wrong, or that it worked. */
export function Feedback({ problem, done = null }: { problem: Trouble; done?: string | null }) {
  return (
    <div aria-live="polite">
      {problem && (
        <p role="alert" className="text-sm text-bad-500">
          {typeof problem === 'string' ? errorText(problem) : problemText(problem)}
        </p>
      )}
      {problem instanceof ApiError && problem.code === 'fresh_sign_in_required' && <SignInAgain providers={problem.values.providers} />}
      {done && (
        <p role="status" className="text-sm text-ok-500">
          {done}
        </p>
      )}
    </div>
  )
}

/** An account that signs in through a provider confirms an operator act with a fresh
 * sign-in there: a button per provider of the account, back to this page afterwards (the server asks the provider for
 * `prompt=login`). */
export function SignInAgain({ providers }: { providers: unknown }) {
  const { t } = useTranslation()
  const list = Array.isArray(providers) ? (providers as { slug: string; label: string }[]) : []
  const here = window.location.pathname + window.location.search
  return (
    <div className="mt-2 flex flex-wrap gap-2" data-testid="sign-in-again">
      {list.map((provider) => (
        <button key={provider.slug} type="button" className="nt-btn nt-btn-ghost h-8 px-3 text-xs" onClick={() => go(startAddress(provider.slug, { next: here, fresh: true }))}>
          {t('oidc.login.button', { name: provider.label })}
        </button>
      ))}
    </div>
  )
}

/** A link or value to copy: the field and the button that copies it. */
export function CopyLink({ value, label }: { value: string; label: string }) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-center gap-2 rounded-[12px] border border-accent-700/40 bg-[var(--color-accent-wash)] p-2">
      <input readOnly value={value} aria-label={label} onFocus={(event) => event.target.select()} className="min-w-0 flex-1 bg-transparent px-2 font-mono text-xs text-mist-100 outline-none" />
      <button
        type="button"
        onClick={() => void copyText(value).then(setCopied)}
        className="nt-btn nt-btn-accent h-8 shrink-0 px-3 text-xs"
      >
        {copied ? '✓' : t('common.copy')}
      </button>
    </div>
  )
}

/** What a `run` job returns when it has asked a question instead of working: no success message, no problem. */
// eslint-disable-next-line react-refresh/only-export-components
export const ASKED = Symbol('asked')

/** Runs a change, remembers whether it worked; the card shows it with `Feedback`. */
// eslint-disable-next-line react-refresh/only-export-components
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Trouble>(null)
  const [done, setDone] = useState<string | null>(null)
  const run = useCallback(async (work: () => Promise<unknown>, success?: string): Promise<boolean> => {
    setBusy(true)
    setProblem(null)
    setDone(null)
    try {
      const result = await work()
      if (result === ASKED) return false
      if (success) setDone(success)
      return true
    } catch (error) {
      // The refusal itself, so its sentence gets the server's values, as on the account page.
      setProblem(error instanceof ApiError ? error : 'internal_error')
      return false
    } finally {
      setBusy(false)
    }
  }, [])
  return { busy, problem, done, run }
}

/** "Really?" with the operator's password where the server asks for it again. */
export function Confirm({ title, text, details, confirm, danger = false, password = false, onCancel, onConfirm }: {
  title: string
  text: string
  /** More below the text, such as what a restore changes. */
  details?: ReactNode
  confirm: string
  danger?: boolean
  password?: boolean
  onCancel: () => void
  onConfirm: (password: string) => Promise<void>
}) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const { busy, problem, run } = useAction()
  return (
    <Dialog title={title} onClose={onCancel}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          void run(() => onConfirm(value))
        }}
      >
        <p className="text-sm text-mist-300">{text}</p>
        {details}
        {password && <input className="nt-field" type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder={t('settings.ownPassword')} autoComplete="current-password" autoFocus />}
        <Feedback problem={problem} />
        <div className="flex justify-end gap-2">
          <button type="button" className="nt-btn nt-btn-ghost" onClick={onCancel}>
            {t('common.cancel')}
          </button>
          <button type="submit" disabled={busy} className={'nt-btn ' + (danger ? 'nt-btn-danger' : 'nt-btn-accent')}>
            {confirm}
          </button>
        </div>
      </form>
    </Dialog>
  )
}

export type Tab<T extends string> = { value: T; label: string; icon?: LucideIcon }

/**
 * A row of round tabs. `under`: a second row below the first, tied to it by a line on the left. The
 * row wraps on a narrow screen.
 */
export function TabRow<T extends string>({ tabs, active, onChange, under = false, label }: { tabs: Tab<T>[]; active: T; onChange: (value: T) => void; under?: boolean; label?: string }) {
  return (
    <div className={'flex flex-wrap items-center gap-2 ' + (under ? 'border-l-2 border-accent-500/40 pl-4' : '')} role="tablist" aria-label={label}>
      {tabs.map(({ value, label: name, icon: Icon }) => {
        const selected = active === value
        return (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(value)}
            className={
              'inline-flex max-w-full items-center gap-2 rounded-[9px] border text-sm font-medium transition-colors ' +
              (under ? 'px-3.5 py-1.5 ' : 'px-4 py-2 ') +
              (selected ? 'nt-chosen border-accent-700/50' : 'border-ink-700 bg-ink-850 text-mist-500 hover:text-mist-100')
            }
          >
            {Icon && <Icon className="h-4 w-4" strokeWidth={1.8} aria-hidden />}
            <span className="truncate" title={name}>
              {name}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function saveAsFile(name: string, data: Blob) {
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
