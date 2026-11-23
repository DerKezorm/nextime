import { SearchX } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { useTitle } from '../lib/title'

/**
 * A link to something that is not there (any more), or not for this account: said in one sentence with the way back,
 * instead of showing something else under the wrong address.
 */
export function NotFound({ text, to = '/', back }: { text: string; to?: string; back?: string }) {
  const { t } = useTranslation()
  return (
    <div data-testid="not-found" className="mx-auto mt-10 max-w-md rounded-[12px] border border-ink-700 bg-ink-850 p-6 text-center">
      <SearchX className="mx-auto h-6 w-6 text-mist-500" strokeWidth={1.8} aria-hidden />
      <p className="mt-3 text-sm text-mist-300">{text}</p>
      <Link to={to} className="nt-btn nt-btn-accent mt-4">
        {back ?? t('notFound.home')}
      </Link>
    </div>
  )
}

/** An address inside nextime no page answers to: said, not silently swapped for the overview. */
export function NotFoundPage() {
  const { t } = useTranslation()
  useTitle(null)
  return (
    <div className="px-4">
      <NotFound text={t('notFound.page')} />
    </div>
  )
}
