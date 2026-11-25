/**
 * About nextime, the ui kit's `AboutPage` (plan "Einstellungen", "Über"): version with "What's new", licence, where it
 * comes from, whether a newer one is out (TIME-1 AC-7, AC-11), and what it is built with. An area of the settings
 * (`/settings/about`, last in the navigation, for every account).
 *
 * The switch for the daily check stands here, where its answer shows, and the page says what goes out: the question
 * to GitHub. Every account sees the answer; switching and asking now belong to the operator, because the question goes
 * out for the whole installation. Times in the account's clock and zone (TIME-3 AC-6).
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api, ApiError } from '../../api/client'
import { errorText, problemText } from '../../lib/errors'
import { moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { useWhatsNew } from '../../lib/whatsNew'
import { useAuth } from '../../state/auth'
import { AboutPage, Notice, type BuiltWith } from '../../vendor/nexui'

type About = { version: string; license: string; repo_url: string; releases_url: string; project_url: string }
type Updates = { update_check: boolean; checked: boolean; latest: string | null; newer: boolean; checked_at: string | null; release_url: string | null }

const PARTS: BuiltWith[] = [
  { name: 'FastAPI', url: 'https://fastapi.tiangolo.com', licence: 'MIT' },
  { name: 'SQLAlchemy', url: 'https://www.sqlalchemy.org', licence: 'MIT' },
  { name: 'SQLite', url: 'https://sqlite.org', licence: 'Public Domain' },
  { name: 'icalendar', url: 'https://github.com/collective/icalendar', licence: 'BSD-2' },
  { name: 'recurring-ical-events', url: 'https://github.com/niccokunzmann/python-recurring-ical-events', licence: 'LGPL-3.0' },
  { name: 'python-dateutil', url: 'https://github.com/dateutil/dateutil', licence: 'Apache-2.0' },
  { name: 'lxml', url: 'https://lxml.de', licence: 'BSD-3' },
  { name: 'Pillow', url: 'https://python-pillow.org', licence: 'MIT-CMU' },
  { name: 'pillow-heif', url: 'https://github.com/bigcat88/pillow_heif', licence: 'BSD-3' },
  { name: 'segno', url: 'https://github.com/heuer/segno', licence: 'BSD-3' },
  { name: 'React', url: 'https://react.dev', licence: 'MIT' },
  { name: 'Vite', url: 'https://vite.dev', licence: 'MIT' },
  { name: 'Tailwind CSS', url: 'https://tailwindcss.com', licence: 'MIT' },
  { name: 'Lucide', url: 'https://lucide.dev', licence: 'ISC' },
  { name: 'i18next', url: 'https://www.i18next.com', licence: 'MIT' },
  { name: 'Inter', url: 'https://rsms.me/inter/', licence: 'OFL-1.1' },
]

/** The refusal itself, so that its sentence gets the values the server sent along. */
function sentence(error: unknown): string {
  return error instanceof ApiError ? problemText(error) : errorText('internal_error')
}

export function AboutArea() {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const { timeOptions } = useDisplay()
  const { entry } = useWhatsNew(me?.version, i18n.language)
  const [about, setAbout] = useState<About | null>(null)
  const [updates, setUpdates] = useState<Updates | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<About>('/api/about').then(setAbout, (error: unknown) => setProblem(sentence(error)))
    // The answer is a side matter: when it does not come, the page simply shows none.
    api<Updates>('/api/about/updates').then(setUpdates, () => setUpdates(null))
  }, [])
  useEffect(load, [load])

  async function checkNow() {
    setBusy(true)
    try {
      setUpdates(await api<Updates>('/api/about/updates/check', { method: 'POST' }))
      setProblem(null)
    } catch (error) {
      setProblem(sentence(error))
    } finally {
      setBusy(false)
    }
  }

  async function switchCheck(on: boolean) {
    if (updates) setUpdates({ ...updates, update_check: on })
    try {
      setUpdates(await api<Updates>('/api/about/updates', { method: 'PUT', body: { update_check: on } }))
    } catch (error) {
      setProblem(sentence(error))
      load()
    }
  }

  if (!about) return problem ? <Notice tone="danger">{problem}</Notice> : null
  return (
    <AboutPage
      app="nextime"
      logo="/logo.svg"
      tagline={t('about.subtitle')}
      version={about.version}
      licence={{ name: about.license, href: 'https://www.gnu.org/licenses/agpl-3.0.html' }}
      source={about.repo_url}
      releases={about.releases_url}
      project={about.project_url || null}
      updates={
        updates
          ? {
              checkDaily: updates.update_check,
              checked: updates.checked,
              latest: updates.latest,
              newer: updates.newer,
              checkedAt: updates.checked_at ? moment(updates.checked_at, i18n.language, false, timeOptions) : null,
              releaseUrl: updates.release_url,
            }
          : null
      }
      operator={me?.role === 'operator'}
      onDaily={(on) => void switchCheck(on)}
      onCheck={() => void checkNow()}
      checking={busy}
      whatsNew={entry ?? null}
      builtWith={PARTS}
      problem={problem}
      texts={{ updatesWhatGoesOut: t('about.updates.whatGoesOut') }}
    />
  )
}
