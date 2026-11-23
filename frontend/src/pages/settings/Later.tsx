/** An area whose content comes with a later feature: it is there and reachable (TIME-2 EC-12) and says so calmly. */
import { Hourglass } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function Later() {
  const { t } = useTranslation()
  return (
    <div className="nt-empty" data-testid="later">
      <Hourglass className="h-6 w-6 text-mist-500" strokeWidth={1.6} aria-hidden />
      <p>{t('account.later')}</p>
    </div>
  )
}
