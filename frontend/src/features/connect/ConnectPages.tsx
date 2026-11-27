/**
 * The pages of the shared module verbinden in nextime (bauplaene/verbinden 0.1.1): `/connect/app`, where another nex
 * app asks for the account's yes to connect (both directions, the calendars to give), and `/connect/return`, where
 * nextime comes back from the other app's consent page and finishes the connection, then on to Connections. Both only
 * with a session of a person (the sign-in first, then back here with the query). Since verbinden 0.1.8 (V48) the pages
 * bring their own frame (`PAGE_FRAME`, the card in the middle of the window); nextime adds none.
 */
import { useTranslation } from 'react-i18next'
import { Navigate, useLocation } from 'react-router-dom'

import { api } from '../../api/client'
import { useAuth } from '../../state/auth'
import { ConsentPage, ReturnPage } from '../../vendor/nexverbinden'
import type { VerbindenRequest } from '../../vendor/nexverbinden/verbinden'

/** The module's calls go through nextime's client (the tab's mark, the session). */
// eslint-disable-next-line react-refresh/only-export-components
export const verbindenRequest: VerbindenRequest = <T,>(path: string, init: Parameters<VerbindenRequest>[1] = {}) => api<T>(path, init)

/** TODO verbinden 0.1.8: `ConsentPage` names `navigate` among the deps of the effect that asks `GET /consent`, and its
 * default (`(url) => window.location.assign(url)`) is a new function at every render: without a `navigate` of its own
 * the page asks again after every answer, without end. One function that stays the same; goes once the module keeps
 * its default stable. */
const go = (url: string) => window.location.assign(url)

function SignedInOnly({ children }: { children: React.ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return null
  if (status === 'setup') return <Navigate to="/setup" replace />
  if (status !== 'signedIn') return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  return <>{children}</>
}

export function ConnectConsentPage() {
  const { t, i18n } = useTranslation()
  const location = useLocation()
  return (
    <SignedInOnly>
      <ConsentPage request={verbindenRequest} search={location.search} navigate={go} areaWord={t('keys.dialog.calendars')} language={i18n.language} />
    </SignedInOnly>
  )
}

export function ConnectReturnPage() {
  const { i18n } = useTranslation()
  const location = useLocation()
  return (
    <SignedInOnly>
      <ReturnPage app="nextime" request={verbindenRequest} search={location.search} language={i18n.language} />
    </SignedInOnly>
  )
}
