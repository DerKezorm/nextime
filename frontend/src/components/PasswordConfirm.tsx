/** The second field of every new password, with its own message right below it when the two differ. */
import { useId } from 'react'
import { useTranslation } from 'react-i18next'

export function PasswordConfirmField({ value, onChange, mismatch, labelClassName = 'font-medium' }: {
  value: string
  onChange: (value: string) => void
  mismatch: boolean
  labelClassName?: string
}) {
  const { t } = useTranslation()
  const messageId = useId()
  return (
    <div className="text-sm">
      <label className="block">
        <span className={labelClassName}>{t('auth.passwordConfirm')}</span>
        <input
          type="password"
          name="password_confirm"
          value={value}
          // Both fields "new-password": the password manager fills the same suggestion into both.
          autoComplete="new-password"
          aria-invalid={mismatch || undefined}
          aria-describedby={mismatch ? messageId : undefined}
          onChange={(event) => onChange(event.target.value)}
          className="nt-field mt-1"
        />
      </label>
      {mismatch && (
        <span id={messageId} role="alert" className="mt-1 block text-xs text-bad-500">
          {t('errors.password_mismatch')}
        </span>
      )}
    </div>
  )
}
