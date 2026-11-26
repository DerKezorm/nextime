/**
 * The pages and words of the shared module mcp (`vendor/nexmcp`, bauplaene/mcp 1.1.0) in nextime: the consent page
 * "Programm verbinden" (`/oauth/authorize`, where an AI program that connects itself over OAuth asks for the account's
 * yes, MCP decision 6), the page of one request that waits for a yes (`/mcp/requests/<id>`), and nextime's words for
 * its seven tools and two levels. The cards' parts ("Rechte je Werkzeug", "Wartende Rückfragen", the operator's
 * settings) are placed in `features/keys/ConnectionsArea.tsx` and `pages/settings/SettingsAreas.tsx`.
 */
import { useTranslation } from 'react-i18next'
import { Navigate, useLocation, useParams } from 'react-router-dom'

import { api } from '../../api/client'
import { useAuth } from '../../state/auth'
import { ConsentPage, RequestPage, type McpRequest, type McpWords } from '../../vendor/nexmcp'

/** The module's calls go through nextime's client (the tab's mark, the session). */
// eslint-disable-next-line react-refresh/only-export-components
export const mcpRequest: McpRequest = <T,>(path: string, init: Parameters<McpRequest>[1] = {}) => api<T>(path, init)

/** The seven tools of nextime (`services/mcp.py`) and its levels, in the words of the page. */
// eslint-disable-next-line react-refresh/only-export-components
export function useMcpWords(): McpWords {
  const { t } = useTranslation()
  const tools = ['list_calendars', 'list_events', 'search_events', 'find_free_time', 'create_event', 'update_event', 'delete_event'] as const
  return {
    tools: Object.fromEntries(tools.map((name) => [name, t(`mcp.tools.${name}`)])),
    levels: { read: t('keys.dialog.read'), write: t('keys.dialog.write') },
    levelHints: { read: t('keys.dialog.readHint'), write: t('keys.dialog.writeHint') },
  }
}

/** "Programm verbinden": only with a session of a person (the sign-in first, then back here with the query). */
export function McpConsentPage() {
  const { t, i18n } = useTranslation()
  const { status } = useAuth()
  const location = useLocation()
  const words = useMcpWords()
  if (status === 'loading') return null
  if (status === 'setup') return <Navigate to="/setup" replace />
  if (status !== 'signedIn') return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />
  return (
    <div className="nt-scroll flex min-h-dvh flex-col items-center overflow-y-auto px-4 py-10" data-testid="mcp-consent-page">
      <ConsentPage request={mcpRequest} search={location.search} areaWord={t('keys.dialog.calendars')} words={words} language={i18n.language} />
    </div>
  )
}

/** One request of an AI program that waits for the account's yes, with everything it would do. */
export function McpRequestPage() {
  const { i18n } = useTranslation()
  const { id } = useParams()
  const words = useMcpWords()
  if (!id) return null
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-4" data-testid="mcp-request-area">
      <RequestPage request={mcpRequest} id={id} words={words} language={i18n.language} />
    </div>
  )
}
