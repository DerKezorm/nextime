/** Server › General and Server › Google while the operator's settings did not come (a 500, no network): why, and
 * "Erneut versuchen", instead of a blank page. Nothing while they are still on their way. */
import { useTranslation } from 'react-i18next'

import { Button, Notice } from '../../vendor/nexui'
import type { Server } from './ServerCards'

export function SettingsLoadProblem({ server }: { server: Server }) {
  const { t } = useTranslation()
  if (!server.loadProblem) return null
  return (
    <div className="flex flex-col items-start gap-3" data-testid="server-settings-problem">
      <Notice tone="danger">{server.loadProblem}</Notice>
      <Button kind="secondary" icon="RefreshCw" onClick={() => void server.reload()} testId="server-settings-retry">
        {t('server.loadRetry')}
      </Button>
    </div>
  )
}
