/**
 * Server › Log (TIME-1 AC-9): the card "Log" of the shared module log (`vendor/nexlog`, bauplaene/log 1.0.0), built
 * from the ui kit. nextime hands it its client, its word for what it keeps ("Kalendern") and its way of writing a moment
 * (the reader's own time, in the clock the account chose under Appearance).
 */
import { useTranslation } from 'react-i18next'

import { api } from '../../api/client'
import { clock, moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { LogCard as SharedLogCard } from '../../vendor/nexlog/LogCard'
import type { LogLine, LogRequest } from '../../vendor/nexlog/log'

const request: LogRequest = <T,>(path: string, init: Parameters<LogRequest>[1] = {}) => api<T>(path, init)

export function LogCard() {
  const { t } = useTranslation()
  const { timeOptions } = useDisplay()
  const when = (line: LogLine, language: string) => (line.at ? moment(line.at, language, true, timeOptions) : line.time)
  return <SharedLogCard request={request} base="/api/logs" things={t('settings.things')} thingsDative={t('settings.thingsDative')} when={when} clock={(iso, language) => clock(iso, language, timeOptions)} />
}
