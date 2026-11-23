/** "What's new" as in nextasks: the banner after an update with the window of the written text, put away for the
 * account on the server; an account that read the version sees no banner, and "About nextime" opens the text again. */
import App from '../App'
import { calendar, calendarList } from '../calendarFakes'
import i18n from '../i18n'
import de from '../i18n/whatsnew/de.json'
import { AuthProvider } from '../state/auth'
import { byText, calls, click, me, ok, render, serve, signedIn, start, stop } from '../testing'

const LISTS = calendarList([calendar(1, 'Persönlich', { default: true })])
const ABOUT = { version: '0.1.0', license: 'AGPL-3.0', repo_url: 'https://github.com/example/nextime', releases_url: 'https://github.com/example/nextime/releases', project_url: '' }

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(() => stop())

function server(seen: string) {
  let account = me({ whats_new_seen: seen })
  serve(
    signedIn(account, (method, path) => {
      if (path === '/api/me/whats-new/seen' && method === 'POST') {
        account = { ...account, whats_new_seen: account.version }
        return ok(account)
      }
      if (path === '/api/auth/me') return ok(account)
      if (path.startsWith('/api/calendars')) return ok(LISTS)
      if (path === '/api/about') return ok(ABOUT)
      if (path === '/api/about/updates') return ok({ update_check: true, checked: false, latest: null, newer: false, checked_at: null, release_url: null })
      return undefined
    }),
  )
}

const banner = () => document.querySelector('[data-testid="whats-new-banner"]')
const window_ = () => document.querySelector('[data-testid="whats-new-window"]')
const seenCalls = () => calls.filter((call) => call.path === '/api/me/whats-new/seen' && call.method === 'POST')

describe("what's new", () => {
  it('shows the banner after an update, opens the text and puts it away for the account', async () => {
    server('')
    await render(<AuthProvider><App /></AuthProvider>)
    expect(banner()?.textContent).toContain('nextime 0.1.0 ist da.')
    await click(byText('Was ist neu?', banner()!))
    const shown = window_()!
    expect(shown.getAttribute('role')).toBe('dialog')
    expect(shown.textContent).toContain('Neu in nextime 0.1.0')
    expect(shown.textContent).toContain(de['0.1.0'].lead)
    for (const part of de['0.1.0'].sections) expect(shown.textContent).toContain(part.title)
    expect(seenCalls()).toHaveLength(0)
    await click(byText('Verstanden', shown))
    expect(window_()).toBeNull()
    expect(banner()).toBeNull()
    expect(seenCalls()).toHaveLength(1)
  })

  it('stays away once read, and About nextime opens the text again without reporting it', async () => {
    server('0.1.0')
    await render(<AuthProvider><App /></AuthProvider>, '/settings/about')
    expect(banner()).toBeNull()
    const row = document.querySelector<HTMLElement>('[data-testid="about-whats-new"]')!
    expect(row.textContent).toBe('Neu in nextime 0.1.0')
    await click(row)
    expect(window_()?.textContent).toContain(de['0.1.0'].sections[0].title)
    await click(byText('Verstanden', window_()!))
    expect(window_()).toBeNull()
    expect(seenCalls()).toHaveLength(0)
  })
})
