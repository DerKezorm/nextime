/**
 * About nextime, as nexnotes' (pages/settings/AboutArea.tsx): version, licence, where it comes from, whether a newer
 * one is out, and what it is built with. An area of the settings (`/settings/about`, last in the navigation, for every
 * account). The heading and its line come from the settings page.
 *
 * The switch for the daily check stands here, where its answer shows, as in nexnotes (TIME-1 AC-11), and the page
 * says what goes out: the question to GitHub. Every account sees the answer; switching and asking now belong to the
 * operator, because the question goes out for the whole installation. Under the version, "What's new" opens the
 * written text of the running version again, as in nextasks.
 */
import { BookOpen, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

import { api, ApiError } from '../../api/client'
import { Logo, Wordmark } from '../../components/Logo'
import { WhatsNewWindow } from '../../components/WhatsNew'
import { moment } from '../../lib/time'
import { useDisplay } from '../../lib/useDisplay'
import { useWhatsNew } from '../../lib/whatsNew'
import { useAuth } from '../../state/auth'
import { Button, Card, Feedback, Toggle } from './ui'

type About = { version: string; license: string; repo_url: string; releases_url: string; project_url: string }
type Updates = { update_check: boolean; checked: boolean; latest: string | null; newer: boolean; checked_at: string | null; release_url: string | null }

const PARTS = [
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

function Out({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent-400 underline decoration-accent-700/40 underline-offset-4 hover:decoration-accent-400">
      {children}
    </a>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 border-b border-ink-700/60 py-2.5 last:border-b-0">
      <dt className="text-sm text-mist-500">{label}</dt>
      <dd className="text-sm font-medium text-mist-100">{children}</dd>
    </div>
  )
}

/** The refusal itself, so that its sentence gets the values the server sent along. */
function code(error: unknown): ApiError | string {
  return error instanceof ApiError ? error : 'internal_error'
}

export function AboutArea() {
  const { t, i18n } = useTranslation()
  const { me } = useAuth()
  const { timeOptions } = useDisplay()
  const operator = me?.role === 'operator'
  const [about, setAbout] = useState<About | null>(null)
  const [updates, setUpdates] = useState<Updates | null>(null)
  const [problem, setProblem] = useState<ApiError | string | null>(null)
  const [busy, setBusy] = useState(false)
  // The written text of the running version, to read again at any time.
  const { entry } = useWhatsNew(me?.version, i18n.language)
  const [reading, setReading] = useState(false)

  const load = useCallback(() => {
    api<About>('/api/about').then(setAbout, (error: unknown) => setProblem(code(error)))
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
      setProblem(code(error))
    } finally {
      setBusy(false)
    }
  }

  async function switchCheck(on: boolean) {
    if (updates) setUpdates({ ...updates, update_check: on })
    try {
      setUpdates(await api<Updates>('/api/about/updates', { method: 'PUT', body: { update_check: on } }))
    } catch (error) {
      setProblem(code(error))
      load()
    }
  }

  const latest = updates?.latest?.replace(/^v/, '')
  return (
    <div className="space-y-6" data-testid="about-area">
      <Feedback problem={problem} />
      <section aria-labelledby="about-facts-title" className="rounded-[12px] border border-ink-700 bg-ink-850 p-5">
        <div className="mb-2 flex items-center gap-3">
          <Logo size={36} />
          <div>
            <h2 id="about-facts-title">
              <Wordmark className="text-lg" />
            </h2>
            <p className="text-sm text-mist-500">{t('about.subtitle')}</p>
          </div>
        </div>
        {about && (
          <dl data-testid="about-facts">
            <Row label={t('about.version')}>
              <span className="flex flex-wrap items-center gap-2">
                <span data-testid="about-version">{about.version}</span>
                {updates?.newer && (
                  <span className="nt-chosen rounded-full px-2 py-0.5 text-xs font-semibold" data-testid="about-newer">
                    {t('about.newer', { version: latest })}
                  </span>
                )}
              </span>
            </Row>
            {entry && me && (
              <Row label={t('about.whatsNew')}>
                <button
                  type="button"
                  onClick={() => setReading(true)}
                  className="text-accent-400 underline decoration-accent-700/40 underline-offset-4 hover:decoration-accent-400"
                  data-testid="about-whats-new"
                >
                  {t('whatsNew.title', { version: me.version })}
                </button>
              </Row>
            )}
            <Row label={t('about.licence')}>
              <Out href="https://www.gnu.org/licenses/agpl-3.0.html">{about.license}</Out>
            </Row>
            <Row label={t('about.source')}>
              <Out href={about.repo_url}>{about.repo_url.replace(/^https:\/\//, '')}</Out>
            </Row>
            <Row label={t('about.releases')}>
              <Out href={about.releases_url}>{t('about.releasesLink')}</Out>
            </Row>
            {about.project_url && about.project_url !== about.repo_url && (
              <Row label={t('about.project')}>
                <Out href={about.project_url}>{about.project_url.replace(/^https:\/\//, '')}</Out>
              </Row>
            )}
            <Row label={t('about.report')}>
              <Out href={`${about.repo_url}/issues/new`}>{t('about.reportLink')}</Out>
            </Row>
          </dl>
        )}
      </section>

      <Card icon={RefreshCw} title={t('about.updates.title')} text={t('about.updates.text')} id="about-updates">
        {operator && updates && <Toggle label={t('about.updates.daily')} hint={t('about.updates.dailyHint')} checked={updates.update_check} onChange={(on) => void switchCheck(on)} />}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm" aria-live="polite">
          {operator && (
            <Button onClick={() => void checkNow()} busy={busy}>
              {t('about.updates.now')}
            </Button>
          )}
          <span data-testid="about-update-state" className="text-mist-400">
            {!updates || !updates.checked ? (
              updates && !updates.update_check ? t('about.updates.off') : t('about.updates.notYet')
            ) : updates.newer ? (
              <>
                {t('about.newer', { version: latest })}
                {updates.release_url && (
                  <>
                    {' · '}
                    <Out href={updates.release_url}>{t('about.updates.toRelease')}</Out>
                  </>
                )}
              </>
            ) : updates.latest ? (
              t('about.updates.current')
            ) : (
              t('about.updates.none')
            )}
          </span>
          {updates?.checked_at && <span className="text-xs text-mist-500">{t('about.updates.checkedAt', { when: moment(updates.checked_at, i18n.language, false, timeOptions) })}</span>}
        </div>
        <p className="text-xs leading-relaxed text-mist-500">{t('about.updates.whatGoesOut')}</p>
      </Card>

      <Card icon={BookOpen} title={t('about.builtWith')}>
        <ul className="flex flex-wrap gap-x-3 gap-y-1.5 text-sm">
          {PARTS.map((part) => (
            <li key={part.name}>
              <Out href={part.url}>{part.name}</Out>
              <span className="ml-1 text-xs text-mist-500">({part.licence})</span>
            </li>
          ))}
        </ul>
      </Card>
      {reading && entry && me && <WhatsNewWindow version={me.version} entry={entry} onClose={() => setReading(false)} />}
    </div>
  )
}
