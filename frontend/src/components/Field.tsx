/** A field with its name above it, and a problem as a red box: the pieces of the account page. */
import { useId } from 'react'

export function Field({
  label,
  value,
  onChange,
  type = 'text',
  autoComplete,
  autoFocus = false,
  hint,
  hints,
  maxLength,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  autoComplete?: string
  autoFocus?: boolean
  hint?: string
  /** More than one hint, each its own line under the field (konten 0.3.7: the username in the profile). */
  hints?: readonly string[]
  /** The server's limit, so a longer text cannot be typed at all. */
  maxLength?: number
}) {
  // The hints describe the field (aria-describedby); they are not part of its name.
  const hintId = useId()
  const lines = (hints ?? (hint ? [hint] : [])).filter(Boolean)
  return (
    <div className="text-sm">
      <label className="block">
        <span className="font-medium text-mist-100">{label}</span>
        <input
          type={type}
          value={value}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          maxLength={maxLength}
          aria-describedby={lines.length ? lines.map((_, index) => `${hintId}-${index}`).join(' ') : undefined}
          onChange={(event) => onChange(event.target.value)}
          className="nt-field mt-1"
        />
      </label>
      {lines.map((line, index) => (
        <span key={index} id={`${hintId}-${index}`} className="mt-1 block text-xs text-mist-500">
          {line}
        </span>
      ))}
    </div>
  )
}

export function Problem({ text }: { text: string | null }) {
  if (!text) return null
  return (
    <p role="alert" className="rounded-[9px] border border-bad-500/30 bg-bad-500/10 px-3 py-2 text-sm text-bad-500">
      {text}
    </p>
  )
}
