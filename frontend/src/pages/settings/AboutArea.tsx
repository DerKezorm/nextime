/**
 * About nextime, the ui kit's `AboutPage` (plan "Einstellungen", "Über"): version with "What's new", licence, where it
 * comes from, whether a newer one is out (TIME-1 AC-7, AC-11), and what it is built with. An area of the settings
 * (`/settings/about`, last in the navigation, for every account).
 *
 * The card "Aktualisierungen" comes from the shared module updates (`vendor/nexupdates`, bauplaene/updates 0.1.3):
 * only an operator sees it, switches the daily check and asks now, because the question goes out for the whole
 * installation; a member sees only the own version (U2, U15). nextime has no bell: the daily hint says when the
 * question goes out by itself, while a device of an operator wants to hear of new versions (U24). The page says what goes out: the question to GitHub,
 * and nextime's other call by itself, the check of the internet connection (U11). Times in the account's clock and
 * zone (TIME-3 AC-6). "Was ist neu" and "Frühere Versionen" come from the shared module wasneu (`vendor/nexwasneu`):
 * reading here marks nothing.
 */
import { useTranslation } from 'react-i18next'

import { tabFetch } from '../../api/client'
import { moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { useAuth } from '../../state/auth'
import { AboutPage, Notice, type BuiltWith } from '../../vendor/nexui'
import { dailyHint, useUpdates, useUpdatesTexts, whatGoesOut, type Fetcher } from '../../vendor/nexupdates/updates'
import { useAboutWhatsNew } from '../../vendor/nexwasneu'

/** nextime's way to the module's routes: every request names its tab (changes are refused without it). */
const fetcher: Fetcher = tabFetch

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

export function AboutArea() {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const { timeOptions } = useDisplay()
  const news = useAboutWhatsNew({ app: 'nextime', logo: '/logo.svg', fetcher: tabFetch, language: i18n.language })
  const operator = me?.role === 'operator'
  const texts = useUpdatesTexts(i18n.language)
  const state = useUpdates({
    fetcher,
    operator,
    language: i18n.language,
    when: (iso) => moment(iso, i18n.language, false, timeOptions),
  })
  const about = state.about

  if (!about) return state.problem ? <Notice tone="danger">{state.problem}</Notice> : null
  return (
    <>
      <AboutPage
        app="nextime"
        logo="/logo.svg"
        tagline={t('about.subtitle')}
        version={about.version}
        licence={{ name: about.license, href: 'https://www.gnu.org/licenses/agpl-3.0.html' }}
        source={about.repo_url}
        releases={about.releases_url}
        project={about.project_url || null}
        updates={state.updates}
        showUpdates={state.showUpdates}
        operator={operator}
        onDaily={state.onDaily}
        onCheck={state.onCheck}
        checking={state.checking}
        {...news.about}
        builtWith={PARTS}
        problem={state.problem}
        texts={{
          updatesWhatGoesOut: whatGoesOut(texts, 'nextime', undefined, [t('about.updates.internetCheck')]),
          updatesDailyHint: dailyHint(texts, { bell: false, devices: true }),
        }}
      />
      {news.window}
    </>
  )
}
