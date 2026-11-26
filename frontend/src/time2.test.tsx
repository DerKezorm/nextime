/** TIME-2 in the interface: the settings with "My account" and its areas and the profile, the group "Server" for the
 * operator, the list of accounts with open invitations, signing out, the password typed twice, names shown as text, a blocked or
 * expired session. */
import { act } from 'react'

import App from './App'
import { api } from './api/client'
import { Avatar } from './components/Avatar'
import i18n from './i18n'
import { personColor } from './lib/avatar'
import { AuthProvider } from './state/auth'
import { box, byText, calls, click, me, ok, render, serve, settle, signedIn, start, stop, type } from './testing'

const OPERATOR = me({ id: 1, name: 'tester', display_name: 'Tester', role: 'operator' })

beforeEach(async () => {
  start()
  await i18n.changeLanguage('de')
})
afterEach(stop)

describe('TIME-2 My account', () => {
  it('TIME-2 AC-7 "Einstellungen" leads to "Mein Konto" with its areas', async () => {
    serve(signedIn(me()))
    await render(<AuthProvider><App /></AuthProvider>, '/')
    await click([...box.querySelectorAll('a')].find((link) => link.getAttribute('aria-label') === 'Anna · Einstellungen'))
    expect(box.querySelector('h1')!.textContent).toBe('Profil')
    const nav = box.querySelector('[data-testid="settings-nav"]')!
    expect(nav.querySelector('#settings-group-account')!.textContent).toBe('Mein Konto')
    const areas = [...nav.querySelectorAll('ul a')].map((entry) => entry.textContent)
    // In the order of the plan "Einstellungen": nextime's own areas after Appearance, then Notifications, Connections.
    expect(areas).toEqual(['Profil', 'Sicherheit', 'Darstellung', 'Kalender und Konten', 'Freigaben', 'Auf iPhone und Co', 'Mitnehmen und umziehen', 'Benachrichtigungen', 'Verbindungen'])
    await click(box.querySelector('[data-testid="settings-nav-shares"]'))
    expect(box.querySelector('h1')!.textContent).toBe('Freigaben')
    // Since TIME-19 the area shows the shares (here none yet), no longer "comes later".
    expect(box.querySelector('[data-testid="shares-area"]')).not.toBeNull()
    expect(box.querySelector('[data-testid="later"]')).toBeNull()
  })

  it('TIME-2 AC-7 the operator finds the group "Server" with "Allgemein", "Konten" and "Google einrichten"', async () => {
    serve(signedIn(OPERATOR))
    await render(<AuthProvider><App /></AuthProvider>, '/')
    await click(box.querySelector('[data-testid="settings-entry"]'))
    const nav = box.querySelector('[data-testid="settings-nav"]')!
    expect(nav.querySelector('#settings-group-server')!.textContent).toBe('Server')
    const server = [...nav.querySelectorAll('ul')][1]
    expect([...server.querySelectorAll('a')].map((entry) => entry.textContent)).toEqual(['Allgemein', 'Anmeldung', 'Mail', 'Speicher und Sicherung', 'Google einrichten', 'Konten', 'Log'])
    // Each entry leads to its area of the settings.
    for (const [entry, area] of [['Allgemein', 'general'], ['Konten', 'accounts'], ['Google einrichten', 'google'], ['Profil', 'profile']]) {
      await click(byText(entry, box.querySelector<HTMLElement>('[data-testid="settings-nav"]')!))
      expect(box.querySelector(`[data-testid="settings-area-${area}"]`), entry).not.toBeNull()
    }
  })

  it('TIME-2 EC-12 in English all areas of "My account" are there, each with its whole name as a tooltip', async () => {
    await i18n.changeLanguage('en')
    serve(signedIn(me({ language: 'en' })))
    await render(<AuthProvider><App /></AuthProvider>, '/settings')
    const entries = [...box.querySelectorAll<HTMLAnchorElement>('[data-testid="settings-nav"] ul a')]
    expect(entries).toHaveLength(9)
    expect(entries.map((entry) => entry.title)).toEqual(['Profile', 'Security', 'Appearance', 'Calendars and accounts', 'Shares', 'On iPhone and co', 'Take along and move', 'Notifications', 'Connections'])
    expect(entries.map((entry) => entry.textContent)).toEqual(entries.map((entry) => entry.title))
    expect(entries.every((entry) => entry.getAttribute('href')?.startsWith('/settings/'))).toBe(true)
    expect(box.querySelector('[data-testid="settings-nav-about"]')!.textContent).toBe('About nextime')
  })

  it('TIME-2 AC-8 the profile changes the name; Security shows the second factor (the line on the way in is gone)', async () => {
    let saved: unknown = null
    serve(signedIn(me({ two_factor: true }), (method, path, body) => {
      if (path === '/api/me/profile' && method === 'PUT') {
        saved = body
        return ok(me({ two_factor: true, display_name: 'Anna Berg' }))
      }
      return undefined
    }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    // Plan "Einstellungen": "Anmeldung mit Passwort / über …" is no longer in the profile; Security says it.
    expect(box.querySelector('[data-testid="sign-in-way"]')).toBeNull()
    const name = [...box.querySelectorAll<HTMLInputElement>('#profile input')].find((field) => field.value === 'Anna')!
    type(name, 'Anna Berg')
    await click(byText('Speichern', box.querySelector('#profile')!))
    // Display name, username and address go with one button (konten 0.3.7); an unchanged username is not sent.
    expect(saved).toEqual({ display_name: 'Anna Berg', email: 'anna@example.com' })
    // Whether the second factor is on, says Security since the shared account module ("An" beside its heading).
    await render(<AuthProvider><App /></AuthProvider>, '/settings/security')
    expect(box.querySelector('[data-testid="second-factor-state"]')!.textContent).toBe('An')
  })

  it('TIME-2 AC-8 an account from authentik finds its provider under Security, linked', async () => {
    serve(signedIn(me({ sign_in: 'oidc' }), (_method, path) => (path === '/api/oidc/me' ? ok([{ slug: 'authentik', label: 'authentik', linked: true, managed: 'authentik' }]) : undefined)))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/security')
    const providers = box.querySelector('[data-testid="my-providers"]')!
    expect(providers.textContent).toContain('authentik')
    expect(providers.textContent).toContain(i18n.t('oidc.account.linked'))
  })

  it('TIME-2 AC-5 "Profilbild hochladen" (as in nexnotes) sends the picture and the hint says where it shows', async () => {
    let picture: string | null = null
    serve((method, path, body) => {
      // The server answers with the account, as `PUT /api/auth/avatar` does.
      if (path === '/api/auth/avatar' && method === 'PUT') {
        picture = '2026-11-22T10:00:00Z'
        return ok(me({ avatar: picture }))
      }
      return signedIn(me({ avatar: picture }))(method, path, body)
    })
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    expect(box.textContent).toContain('Profilbild hochladen')
    // Who sees it, in the words of the shared account module (konten 0.3.6, `PAGES.profile`).
    expect(box.textContent).toContain(i18n.t('account.pictureHint'))
    const input = box.querySelector<HTMLInputElement>('[data-testid="picture-file"]')!
    const file = new File([new Uint8Array([0xff, 0xd8, 0xff])], 'me.jpg', { type: 'image/jpeg' })
    Object.defineProperty(input, 'files', { value: [file] })
    await act(async () => void input.dispatchEvent(new Event('change', { bubbles: true })))
    await settle()
    expect(calls.some((call) => call.method === 'PUT' && call.path === '/api/auth/avatar')).toBe(true)
    expect(box.querySelector('[data-testid="profile-picture"] img')!.getAttribute('src')).toContain('/api/avatars/1')
    // With a picture, as the module says: "Profilbild hochladen" stays, "Bild entfernen" comes.
    const shownPicture = box.querySelector<HTMLElement>('[data-testid="profile-picture"]')!
    expect(shownPicture.textContent).toContain('Profilbild hochladen')
    expect(byText('Bild entfernen', shownPicture)).toBeDefined()
  })

  it('TIME-2 AC-9 "Abmelden" ends the session and leads to the sign-in page', async () => {
    let out = false
    serve((method, path, body) => {
      if (path === '/api/auth/logout') {
        out = true
        return { status: 204, body: null }
      }
      if (out && path === '/api/setup') return ok({ needs_setup: false, code_required: true, signed_in: false, version: '0.1.0', min_password: 12 })
      return signedIn(me())(method, path, body)
    })
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    await click(box.querySelector('[data-testid="sign-out"]'))
    expect(calls.some((call) => call.path === '/api/auth/logout' && call.method === 'POST')).toBe(true)
    expect(box.querySelector('[data-testid="settings-nav"]')).toBeNull()
    expect(box.querySelector('input[type="password"]')).not.toBeNull()
  })
})

describe('TIME-2 the accounts of the server', () => {
  const ROWS = [
    { id: 1, name: 'tester', display_name: 'Tester', email: 'tester@example.com', role: 'operator', sign_in: 'password', language: '', two_factor: false, created_at: '2026-11-01T00:00:00Z', last_seen_at: '2026-11-22T09:00:00Z', locked: false, blocked: false, blocked_at: null, deleted_at: null, purge_at: null, has_password: true, providers: [], storage_used: 1024, storage_limit: null },
    { id: 2, name: 'anna', display_name: '<b>Anna</b> 🦄', email: 'anna@example.com', role: 'member', sign_in: 'password', language: '', two_factor: false, created_at: '2026-11-02T00:00:00Z', last_seen_at: null, locked: false, blocked: false, blocked_at: null, deleted_at: null, purge_at: null, has_password: true, providers: [], storage_used: 0, storage_limit: null },
  ]
  const INVITES = [
    { id: 5, role: 'member', email: 'ben@example.com', by: 'Tester', created_at: '2026-11-20T00:00:00Z', expires_at: '2099-11-27T00:00:00Z' },
    { id: 6, role: 'operator', email: '', display_name: 'Lena', by: 'Tester', created_at: '2026-11-01T00:00:00Z', expires_at: '2026-11-02T00:00:00Z', expired: true },
  ]

  function accounts() {
    return signedIn(OPERATOR, (_method, path) => {
      if (path === '/api/accounts') return ok(ROWS)
      if (path === '/api/invites') return ok(INVITES)
      if (path === '/api/accounts/2/impact') return ok({ calendars: 2, events: 17, shared: 1, days: 30 })
      return undefined
    })
  }

  it('TIME-2 AC-3 AC-6 lists every person with picture, badges and "Verwalten", the open invitations, and the sentence below', async () => {
    serve(accounts())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/accounts')
    // The list of the shared account module (konten 0.3.9): one row per account, no table, no menu.
    expect(box.querySelector('table')).toBeNull()
    expect(box.querySelector('#accounts')!.textContent).toContain('2 Konten')
    const own = box.querySelector('[data-testid="account-tester"]')!
    expect(own.textContent).toContain('Betreiber')
    expect(own.textContent).toContain('Du')
    expect(own.textContent).toContain('tester · tester@example.com')
    expect(byText('Verwalten', own)).toBeDefined()
    // The invitations as the module's card, an expired one stays until withdrawn.
    const invites = [...box.querySelectorAll('[data-testid="invite-row"]')]
    expect(invites[0].textContent).toContain('ben@example.com')
    expect(invites[0].textContent).toContain('Gilt bis')
    expect(invites[1].textContent).toContain('Lena')
    expect(invites[1].textContent).toContain('Abgelaufen')
    expect(byText('Zurücknehmen', invites[1] as HTMLElement)).toBeDefined()
    // "Was du siehst" of the account module with nextime's sentence (plan "Einstellungen", Konten 3).
    expect(box.querySelector('[data-testid="accounts-privacy"] [data-card] p')!.textContent).toBe('Du siehst Namen, Speicher und Anmeldung, nie Kalender, Termine oder Zugangsdaten.')
  })

  it('TIME-2 AC-6 "Verwalten" shows the facts with the storage and the acts in their groups, nothing forbidden on the own account', async () => {
    serve(accounts())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/accounts')
    await click(byText('Verwalten', box.querySelector<HTMLElement>('[data-testid="account-anna"]')!))
    const dialog = document.querySelector<HTMLElement>('[data-testid="manage-dialog"]')!
    expect(dialog.querySelector('[data-testid="fact-storage"]')!.textContent).toContain('ohne Grenze')
    expect([...dialog.querySelectorAll('h3')].map((head) => head.textContent)).toEqual(['Ändern', 'Zugang', 'Beenden'])
    const change = dialog.querySelector<HTMLElement>('[data-testid="manage-change"]')!
    expect([...change.querySelectorAll('button')].map((button) => button.textContent)).toEqual([
      // No "Speichergrenze" any more: nextime has no storage limit per account (plan "Einstellungen", E24).
      'Anzeigenamen ändern', 'Benutzernamen ändern', 'Zum Betreiber machen', 'Adresse entfernen',
    ])
    await click(byText('Schließen', dialog) ?? dialog.querySelector<HTMLElement>('button[aria-label]'))
    await click(byText('Verwalten', box.querySelector<HTMLElement>('[data-testid="account-tester"]')!))
    const mine = document.querySelector<HTMLElement>('[data-testid="manage-dialog"]')!
    for (const word of ['Sperren', 'Konto löschen', 'Zum Mitglied machen', 'Link erstellen']) expect(byText(word, mine)).toBeUndefined()
  })

  it('TIME-2 EC-7 a name with code shows as text, umlauts and emoji stay', async () => {
    serve(accounts())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/accounts')
    const row = box.querySelector('[data-testid="account-anna"]')!
    expect(row.textContent).toContain('<b>Anna</b> 🦄')
    expect(row.querySelector('b')).toBeNull()
  })

  it('TIME-2 AC-4 deleting asks with the number of calendars, events and shared calendars and the 30 days', async () => {
    serve(accounts())
    await render(<AuthProvider><App /></AuthProvider>, '/settings/accounts')
    await click(byText('Verwalten', box.querySelector<HTMLElement>('[data-testid="account-anna"]')!))
    await click(byText('Konto löschen', document.querySelector<HTMLElement>('[data-testid="manage-dialog"]')!))
    const dialogs = [...document.querySelectorAll('[role="dialog"]')]
    const dialog = dialogs[dialogs.length - 1]!
    // The module's sentence, then nextime's numbers, then how long it can come back (konten 0.3.9, `removeLines`).
    expect(dialog.textContent).toContain('wird gelöscht')
    expect(dialog.querySelector('[data-testid="remove-numbers"]')!.textContent).toContain('2 Kalender mit 17 Termine und 1 geteilten Kalender')
    expect(dialog.textContent).toContain('30 Tage lang kannst du es zurückholen')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
  })

  it('TIME-2 EC-11 two people with the same initial differ by the color the server gave them', async () => {
    serve(() => undefined)
    // Ids 2 and 13 lie eleven apart: the color comes from the server's choice, not from the id.
    await render(
      <div>
        <Avatar accountId={2} color={0} name="Anna" />
        <Avatar accountId={13} color={1} name="Arne" />
      </div>,
    )
    const circles = [...box.querySelectorAll<HTMLElement>('[data-testid="avatar-initial"]')]
    expect(circles.map((circle) => circle.textContent)).toEqual(['A', 'A'])
    expect(circles[0].style.background).not.toBe(circles[1].style.background)
    expect(new Set(Array.from({ length: 11 }, (_, index) => personColor(index))).size).toBe(11)
  })
})

describe('TIME-2 passwords and sessions', () => {
  it('TIME-2 AC-2 a wrong address or password says "Benutzername, E-Mail oder Passwort stimmt nicht."', async () => {
    serve((method, path) => {
      if (path === '/api/setup') return ok({ needs_setup: false, code_required: true, signed_in: false, version: '0.1.0', min_password: 12 })
      if (path === '/api/auth/methods') return ok({ password: true, providers: [] })
      if (path === '/api/auth/login' && method === 'POST') return { status: 401, body: { detail: { code: 'wrong_credentials' } } }
      return undefined
    })
    await render(<AuthProvider><App /></AuthProvider>, '/login')
    const fields = [...box.querySelectorAll('input')]
    type(fields[0], 'anna@example.com')
    type(fields[1], 'a wrong password')
    await act(async () => box.querySelector('form')!.requestSubmit())
    await settle()
    // One sentence for all of it, the shared account module's (`account.wrong`).
    expect(box.textContent).toContain('Benutzername, E-Mail oder Passwort stimmt nicht.')
  })

  it('TIME-2 AC-10 the second password field says when the two differ and nothing is sent', async () => {
    serve((_method, path) => (path === '/api/setup' ? ok({ needs_setup: true, code_required: true, signed_in: false, version: '0.1.0', min_password: 12, https: true }) : undefined))
    await render(<AuthProvider><App /></AuthProvider>, '/setup')
    // Code, display name, username, address, password, password again (konten `SCREENS.setup`).
    const fields = [...box.querySelectorAll('input')]
    type(fields[0], 'AB12-CD34-EF56')
    type(fields[1], 'Robin')
    type(fields[4], 'twelve chars')
    type(fields[5], 'twelve charz')
    await click(byText('Einrichten', box))
    expect(calls.some((call) => call.method === 'POST')).toBe(false)
    const confirm = fields[5]
    expect(confirm.getAttribute('aria-invalid')).toBe('true')
    // Eleven characters: said before anything is sent.
    type(fields[4], 'eleven char')
    type(fields[5], 'eleven char')
    await click(byText('Einrichten', box))
    expect(calls.some((call) => call.method === 'POST')).toBe(false)
    expect(box.textContent).toContain('12')
  })

  it('TIME-2 EC-5 a blocked account sees "Dein Konto ist gesperrt" in its open window', async () => {
    serve(signedIn(me(), (_method, path) => (path === '/api/probe' ? { status: 401, body: { detail: { code: 'account_blocked' } } } : undefined)))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    await act(async () => void (await api('/api/probe', { method: 'PUT', body: {} }).catch(() => undefined)))
    await settle()
    expect(document.body.textContent).toContain(i18n.t('session.blockedTitle'))
  })

  it('TIME-2 EC-4 a session that ran out asks to sign in again and the page stays as it was', async () => {
    let expired = true
    serve(signedIn(me(), (method, path) => {
      if (path === '/api/probe' && expired) return { status: 401, body: { detail: { code: 'sign_in_required' } } }
      if (path === '/api/auth/login' && method === 'POST') {
        expired = false
        return ok(me())
      }
      return undefined
    }))
    await render(<AuthProvider><App /></AuthProvider>, '/settings/profile')
    const name = [...box.querySelectorAll<HTMLInputElement>('#profile input')].find((field) => field.value === 'Anna')!
    type(name, 'Anna typed this')
    await act(async () => void (await api('/api/probe', { method: 'PUT', body: {} }).catch(() => undefined)))
    await settle()
    const dialog = document.querySelector<HTMLFormElement>('[data-testid="session-expired"]')!
    expect(dialog).not.toBeNull()
    type(dialog.querySelector<HTMLInputElement>('input[type="password"]')!, 'twelve chars')
    await act(async () => dialog.requestSubmit())
    await settle()
    expect(document.querySelector('[data-testid="session-expired"]')).toBeNull()
    expect(name.value).toBe('Anna typed this')
  })
})
