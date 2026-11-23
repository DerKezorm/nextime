/**
 * A choice that is on or off, outside the settings cards: the box on the left, its name beside it and a quiet hint
 * below. The same native checkbox in the accent as `Toggle` in the settings, never a sliding switch (decided while
 * testing, 2026-11-23). No `role="switch"`: it is read out as the checkbox it looks like (review, 2026-11-23).
 */
import { useId } from 'react'

export function CheckLine({
  label,
  hint,
  checked,
  onChange,
  disabled = false,
  testId,
  className = '',
}: {
  label: string
  hint?: string
  checked: boolean
  onChange: (value: boolean) => void
  disabled?: boolean
  testId?: string
  className?: string
}) {
  const hintId = useId()
  return (
    <label className={'flex min-w-0 items-start gap-2.5 text-sm ' + (disabled ? 'cursor-not-allowed opacity-60 ' : 'cursor-pointer ') + className}>
      <input
        type="checkbox"
        checked={checked}
        aria-checked={checked}
        aria-label={label}
        aria-describedby={hint ? hintId : undefined}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-[1px] h-[18px] w-[18px] shrink-0 accent-[var(--color-accent-500)]"
        data-testid={testId}
      />
      <span className="min-w-0">
        <span className="text-mist-100">{label}</span>
        {hint && (
          <span id={hintId} className="block text-xs text-mist-500">
            {hint}
          </span>
        )}
      </span>
    </label>
  )
}
