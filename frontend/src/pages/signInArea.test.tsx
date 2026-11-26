// Settings, Server, Sign-in as nextime shows it (plan "Einstellungen", Anmeldung): konten's page ("Ready for the
// internet?", the card "Sign-in") and the module oidc's cards after it, through nextime' own calls. The provider form
// with its fields in their order, the confirmations with their numbers, the authentik steps by name with the reason of
// a failure in words, the second factor for everybody with the module's rules. The step keys come from the shared
// module's own list (`STEP_KEYS` in backend/app/vendor/nexoidc/authentik.py), so a name the language files spell
// differently shows up here instead of on the operator's screen.
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import type { OidcProvider } from '../api/client'
import i18n from '../i18n'
import { SignInArea } from './settings/SignInArea'

/** Whether the operator has an own second factor; the requirement switch is locked without one. */
const viewer = vi.hoisted(() => ({ two_factor: true }))
vi.mock('../state/auth', () => ({
  useAuth: () => ({ me: { id: 1, name: 'operator', role: 'operator', sign_in: 'password', two_factor: viewer.two_factor } }),
  useAccountId: () => 1,
}))

type Files = { readFileSync: (file: string, encoding: string) => string }
const fsName = 'node:fs'
const files = (await import(/* @vite-ignore */ fsName)) as Files

const service = files.readFileSync('../backend/app/vendor/nexoidc/authentik.py', 'utf-8')
const STEP_KEYS = [...(service.match(/^STEP_KEYS = \(([^)]*)\)/m)?.[1] ?? '').matchAll(/"(\w+)"/g)].map((found) => found[1])
type Step = { key: string; ok: boolean; detail: string; reason?: string; status?: number }

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

function entry(change: Partial<OidcProvider> = {}): OidcProvider {
  return {
    id: 1,
    slug: 'sso',
    label: 'Company SSO',
    issuer: 'https://sso.example.com',
    client_id: 'client',
    has_secret: true,
    scopes: 'openid profile email',
    enabled: true,
    auto_create: false,
    trusts_second_factor: true,
    managed: '',
    position: 0,
    redirect_uri: 'https://time.example.com/api/oidc/sso/callback',
    links: 2,
    editable: true,
    ...change,
  }
}

/** What the server answers next, and a gate that holds the setup answer back while a test looks at the page. */
let answer: Step[] = []
/** With ``count`` the setup answer carries the number of an issuer change; ``afterConfirm`` is what the run answers once it is sent with ``confirm_issuer_change``. */
let count: number | undefined
let afterConfirm: Step[] | null = null
let list: OidcProvider[] = []
let settings = { password_login: true, factor_required: false, passkeys_allowed: true, public_url: 'https://time.example.com' }
/** The answer of `GET /api/server/sign-in`: the switches, what may change (from the list and the viewer). */
const view = () => ({
  ...settings,
  password_may_go_off: list.some((entry) => entry.enabled),
  factor_may_go_on: viewer.two_factor,
  second_factor_off: false,
  operator_has_password: true,
})
const READY = {
  state: 'bad',
  open: 1,
  points: [
    { key: 'address', state: 'ok', title: 'account.ready.address.title', text: 'account.ready.address.ok', values: { address: 'https://time.example.com', app: 'nextime' } },
    { key: 'ownAccount', state: 'bad', title: 'account.ready.ownAccount.title', text: 'account.ready.ownAccount.bad', values: {} },
  ],
}
/** What `GET /api/oidc/authentik/address` says; the browser of the test is at http://localhost:3000. */
const ADDRESS = {
  public_url: 'https://time.example.com',
  seen: 'https://time.example.com',
  forwarded_proto: '',
  forwarded_host: '',
  proxy_ignored: false,
  callback_path: '/api/oidc/authentik/callback',
  redirect_uri: 'https://time.example.com/api/oidc/authentik/callback',
  registered_redirect_uri: '',
  stale: false,
}
let addressView: Record<string, unknown> = ADDRESS
/** When set, saving the address (settings or the card's second button) is answered with this refusal until it comes with the confirmation. */
let refusal: { status: number; detail: Record<string, unknown> } | null = null
let gate: Promise<void> = Promise.resolve()
let calls: { method: string; path: string; body: unknown }[] = []
let root: Root
let box: HTMLDivElement

beforeEach(() => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  answer = []
  viewer.two_factor = true
  count = undefined
  afterConfirm = null
  list = []
  settings = { password_login: true, factor_required: false, passkeys_allowed: true, public_url: 'https://time.example.com' }
  addressView = ADDRESS
  refusal = null
  gate = Promise.resolve()
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null })
      if (path === '/api/settings' && method === 'PUT') {
        const sent = JSON.parse(String(init?.body ?? '{}')) as { public_url?: string; confirm_address?: boolean }
        if (refusal && !sent.confirm_address) return json({ detail: refusal.detail }, refusal.status)
        if (sent.public_url !== undefined) settings = { ...settings, public_url: sent.public_url }
        return json(settings)
      }
      if (path === '/api/settings') return json(settings)
      if (path === '/api/server/sign-in/readiness') return json(READY)
      if (path === '/api/server/sign-in' && method === 'PUT') {
        const { current_password: password, ...change } = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        if (change.factor_required === false && password !== 'secret') return json({ detail: { code: 'wrong_password', message: 'x' } }, 401)
        settings = { ...settings, ...change }
        return json(view())
      }
      if (path === '/api/server/sign-in') return json(view())
      if (path === '/api/oidc/authentik/address' && method === 'GET') return json(addressView)
      if (path === '/api/oidc/authentik/address' && method === 'POST') {
        const sent = JSON.parse(String(init?.body ?? '{}')) as { public_url: string; confirm?: boolean }
        if (refusal && !sent.confirm) return json({ detail: refusal.detail }, refusal.status)
        addressView = { ...ADDRESS, public_url: sent.public_url, redirect_uri: sent.public_url + ADDRESS.callback_path }
        return json(addressView)
      }
      if (path === '/api/oidc/admin/providers' && method === 'GET') return json(list)
      if (path.endsWith('/impact')) return json({ issuer_change: 0, count: 3, only: 1, only_names: ['kim'] })
      if (path === '/api/oidc/authentik/setup') {
        await gate
        const confirmed = (JSON.parse(String(init?.body ?? '{}')) as { confirm_issuer_change?: boolean }).confirm_issuer_change === true
        const steps = confirmed && afterConfirm ? afterConfirm : answer
        return json({ ok: steps.every((step) => step.ok) && steps.length === STEP_KEYS.length, steps, client_id: '', issuer: '', provider_id: null, links_dropped: 0, ...(!confirmed && count !== undefined ? { count } : {}) })
      }
      return new Response('{}', { status: 404 })
    }),
  )
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
})

afterEach(async () => {
  act(() => root.unmount())
  box.remove()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
  await i18n.changeLanguage('en')
})

function SignIn() {
  return <SignInArea />
}

const settle = () => act(async () => new Promise((resolve) => setTimeout(resolve, 0)))

function type(input: Element | null | undefined, value: string) {
  const field = input as HTMLInputElement
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
  act(() => {
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const authentikCard = () => document.getElementById('sign-in-authentik')!
const tokenField = () => [...authentikCard().querySelectorAll<HTMLInputElement>('input[type="password"]')].at(-1)!
/** Each step as before the module's cards: ✓ or ✗ (the symbol, `data-ok`), then its name and the reason. */
const rows = () => [...document.querySelectorAll<HTMLElement>('[data-testid="authentik-steps"] li')].map((row) => `${row.dataset.ok === 'true' ? '✓' : '✗'}${row.textContent}`)
const button = (scope: ParentNode, text: string) => [...scope.querySelectorAll('button')].find((found) => found.textContent === text)

async function press(scope: ParentNode, text: string) {
  const found = button(scope, text)
  expect(found, text).toBeDefined()
  await act(async () => found!.click())
}

async function page(language: 'en' | 'de' = 'en') {
  await act(async () => {
    await i18n.changeLanguage(language)
    root.render(<SignIn />)
  })
  await settle()
}

/** The card in `language`, with address and token typed in; the button not yet pressed. */
async function card(language: 'en' | 'de') {
  await page(language)
  type(authentikCard().querySelector('input[placeholder="https://auth.example.com"]'), 'https://auth.example.com')
  type(tokenField(), 'token')
}

const passwordBox = () => box.querySelector<HTMLInputElement>('[data-testid="sign-in-password"]')!

describe('the cards', () => {
  it('stand in the blueprint order with their titles, and the password switch stays on while no provider is active', async () => {
    list = [entry({ enabled: false })]
    await page()
    expect([...box.querySelectorAll('[data-card]')].map((card) => card.getAttribute('data-card'))).toEqual(['sign-in-ready', 'sign-in', 'sign-in-providers', 'sign-in-authentik'])
    const titles = [...box.querySelectorAll('section h2')].map((title) => title.textContent)
    expect(titles).toEqual([i18n.t('account.ready.title'), i18n.t('oidc.admin.signInTitle'), i18n.t('oidc.admin.providersTitle'), i18n.t('oidc.authentik.title')])
    // The label names the box.
    expect(box.querySelector(`label[for="${passwordBox().id}"]`)!.textContent).toBe(i18n.t('oidc.admin.password'))
    expect(passwordBox().checked).toBe(true)
    expect(passwordBox().disabled).toBe(true)
  })

  it('lets the password switch go once a provider is active, and sends it to the sign-in route', async () => {
    list = [entry()]
    await page()
    expect(passwordBox().disabled).toBe(false)
    await act(async () => passwordBox().click())
    await settle()
    expect(calls.filter((call) => call.method === 'PUT' && call.path === '/api/server/sign-in').map((call) => call.body)).toEqual([
      { password_login: false, current_password: '' },
    ])
    expect(passwordBox().checked).toBe(false)
  })

  it('shows "Ready for the internet?" with its open points, every one closed at first', async () => {
    await page()
    const ready = box.querySelector('[data-card="sign-in-ready"]')!
    expect(ready.querySelector('[data-testid="ready-summary"]')!.textContent).toBe(i18n.t('account.ready.open', { count: 1 }))
    const heads = [...ready.querySelectorAll('[aria-expanded]')]
    expect(heads.map((head) => head.textContent)).toEqual([i18n.t('account.ready.address.title', { app: 'nextime' }), i18n.t('account.ready.ownAccount.title')].map((title) => expect.stringContaining(title)))
    expect(heads.map((head) => head.getAttribute('aria-expanded'))).toEqual(['false', 'false'])
  })

  it('lists each entry with its issuer, marks, buttons and a handle to drag; empty, it says how to begin', async () => {
    await page()
    expect(box.textContent).toContain(i18n.t('oidc.admin.empty'))
    act(() => root.unmount())
    root = createRoot(box)
    list = [entry({ managed: 'authentik', label: 'authentik', slug: 'authentik' }), entry({ id: 2, slug: 'entra', label: 'Microsoft', enabled: false })]
    await page()
    const items = [...box.querySelectorAll('[data-testid="provider-list"] li')]
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain('https://sso.example.com')
    expect(items[0].textContent).toContain(i18n.t('oidc.admin.active'))
    expect(items[0].textContent).toContain(i18n.t('oidc.admin.managed'))
    expect(items[1].textContent).toContain(i18n.t('oidc.admin.inactive'))
    expect(items[0].querySelector(`button[aria-label="${i18n.t('oidc.admin.order', { name: 'authentik' })}"] svg`)).not.toBeNull()
    expect(button(items[1], i18n.t('oidc.admin.edit'))).toBeDefined()
    expect(button(items[1], i18n.t('oidc.admin.remove'))).toBeDefined()
  })

  it('opens the form as a dialog with the fields in the blueprint order; the short name follows the name', async () => {
    await page()
    await press(box, i18n.t('oidc.admin.add'))
    const form = document.querySelector('[data-testid="provider-form"]')!
    // Every field by its label (the boxes too), the return address by its heading.
    const labels = [...form.querySelectorAll('label, span[aria-hidden="true"]')].map((node) => node.textContent)
    expect(labels.filter((label) => label !== i18n.t('oidc.admin.scopes'))).toEqual([
      i18n.t('oidc.admin.label'),
      i18n.t('oidc.admin.slug'),
      i18n.t('oidc.admin.issuer'),
      i18n.t('oidc.admin.clientId'),
      i18n.t('oidc.admin.secret'),
      i18n.t('oidc.admin.redirect'),
      i18n.t('oidc.admin.enabled'),
      i18n.t('oidc.admin.autoCreate'),
      i18n.t('oidc.admin.trustsSecondFactor'),
    ])
    type(form.querySelector('input'), 'Größe Firma')
    const fields = [...form.querySelectorAll<HTMLInputElement>('input')]
    expect(fields[1].value).toBe('groesse-firma')
    expect(fields.find((field) => field.readOnly)!.value).toBe('https://time.example.com/api/oidc/groesse-firma/callback')
    const switches = [...form.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].map((field) => field.checked)
    expect(switches).toEqual([true, false, true])
  })

  it('asks before removing an entry, with how many accounts lose the link and sign in only through it', async () => {
    list = [entry()]
    await page()
    await press(box, i18n.t('oidc.admin.remove'))
    await settle()
    const dialog = document.querySelector('[role="dialog"]')!
    expect(dialog.textContent).toContain(i18n.t('oidc.admin.removeConfirm', { name: 'Company SSO', count: 3, only: 1 }))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
  })
})

describe('the second factor requirement', () => {
  const factorBox = () => box.querySelector<HTMLInputElement>('[data-testid="sign-in-factor"]')!
  const hint = () => document.getElementById(factorBox().getAttribute('aria-describedby') ?? '')?.textContent

  for (const language of ['de', 'en'] as const) {
    it(`goes on only with an own second factor, and says so (${language})`, async () => {
      viewer.two_factor = false
      await page(language)
      expect(factorBox().disabled).toBe(true)
      // Since E36 the hint says who is not affected: whoever comes through a provider that checks it itself.
      expect(hint()).toBe(`${i18n.t('account.ownSecondFactorFirst')} ${i18n.t('account.factorRequiredExemptLocked', { trusts: i18n.t('oidc.admin.trustsSecondFactor') })}`)
      act(() => root.unmount())
      root = createRoot(box)
      viewer.two_factor = true
      await page(language)
      expect(factorBox().disabled).toBe(false)
      expect(hint()).toBe(`${i18n.t('account.factorRequiredSwitchHint')} ${i18n.t('account.factorRequiredExempt', { trusts: i18n.t('oidc.admin.trustsSecondFactor') })}`)
      await act(async () => factorBox().click())
      await settle()
      expect(calls.filter((call) => call.method === 'PUT' && call.path === '/api/server/sign-in').map((call) => call.body)).toEqual([
        { factor_required: true, current_password: '' },
      ])
    })
  }

  it('goes off only with the own password: a wrong one changes nothing, the right one turns it off', async () => {
    settings = { ...settings, factor_required: true }
    await page()
    expect(factorBox().checked).toBe(true)
    await act(async () => factorBox().click())
    const dialog = () => document.querySelector('[data-testid="sign-in-factor-off"]')
    expect(dialog()!.textContent).toContain(i18n.t('account.factorRequiredOffText'))
    expect(calls.some((call) => call.method === 'PUT')).toBe(false)
    const confirm = () => button(dialog()!, i18n.t('account.factorRequiredOff'))!
    expect(confirm().disabled).toBe(true)
    type(dialog()!.querySelector('input[type="password"]'), 'wrong')
    await act(async () => confirm().click())
    await settle()
    expect(dialog()!.textContent).toContain(i18n.t('account.error.wrong_password'))
    expect(settings.factor_required).toBe(true)
    type(dialog()!.querySelector('input[type="password"]'), 'secret')
    await act(async () => confirm().click())
    await settle()
    expect(dialog()).toBeNull()
    expect(factorBox().checked).toBe(false)
    expect(calls.filter((call) => call.method === 'PUT').map((call) => call.body)).toEqual([
      { factor_required: false, current_password: 'wrong' },
      { factor_required: false, current_password: 'secret' },
    ])
  })
})

describe('the authentik steps', () => {
  it('come from the module list', () => {
    expect(STEP_KEYS).toEqual(['reached', 'signingKey', 'mapping', 'provider', 'application', 'filled'])
  })

  it.each(['en', 'de'] as const)('are named in %s, and the token goes once all worked', async (language) => {
    answer = STEP_KEYS.map((key) => ({ key, ok: true, detail: `found ${key}` }))
    await card(language)
    await press(authentikCard(), i18n.t('oidc.authentik.run'))
    await settle()
    expect(rows()).toEqual(STEP_KEYS.map((key) => `✓${i18n.t(`oidc.authentik.step.${key}`)}`))
    expect(tokenField().value).toBe('')
  })

  it.each(['en', 'de'] as const)('say in %s why a step failed, by its name, and keep the token for the next try', async (language) => {
    const detail = 'POST /crypto/certificatekeypairs/generate/ answered 403 (application/json); the log has the answer'
    answer = [
      { key: STEP_KEYS[0], ok: true, detail: 'authentik 2026.8.1' },
      { key: STEP_KEYS[1], ok: false, detail, reason: 'token', status: 403 },
    ]
    await card(language)
    await press(authentikCard(), i18n.t('oidc.authentik.run'))
    await settle()
    const why = i18n.t('oidc.authentik.why.token', { status: 403 })
    expect(rows()).toEqual([`✓${i18n.t('oidc.authentik.step.reached')}`, `✗${i18n.t('oidc.authentik.step.signingKey')}${why}`])
    expect(box.textContent).not.toContain(detail)
    expect(tokenField().value).toBe('token')
  })

  it('says authentik is being asked while the run is under way, and the last answer goes first', async () => {
    answer = [{ key: STEP_KEYS[0], ok: false, detail: 'not reachable', reason: 'unreachable', status: 0 }]
    await card('en')
    await press(authentikCard(), i18n.t('oidc.authentik.run'))
    await settle()
    expect(rows()).toHaveLength(1)
    let release = () => undefined as void
    gate = new Promise((resolve) => {
      release = resolve
    })
    await press(authentikCard(), i18n.t('oidc.authentik.run'))
    expect(authentikCard().querySelector('[role="status"]')?.textContent).toBe(i18n.t('oidc.authentik.asking'))
    expect(rows()).toEqual([])
    release()
    await settle()
    expect(authentikCard().querySelector('[role="status"]')).toBeNull()
    expect(rows()).toEqual([`✗${i18n.t('oidc.authentik.step.reached')}${i18n.t('oidc.authentik.why.unreachable')}`])
  })

  describe('the question before an issuer change (module 1.0.8)', () => {
    const stopped: Step[] = [
      { key: STEP_KEYS[0], ok: false, detail: 'another issuer', reason: 'issuer_change', status: 0 },
    ]
    const dialog = () => document.querySelector('[role="dialog"]')

    it.each(['en', 'de'] as const)('asks in %s with the number of accounts and sends nothing more until it is answered yes', async (language) => {
      answer = stopped
      count = 7
      await card(language)
      await press(authentikCard(), i18n.t('oidc.authentik.run'))
      await settle()
      expect(dialog()!.textContent).toContain(i18n.t('oidc.admin.issuerChangeConfirm', { count: 7 }))
      // Since E35 only the question asks: no line of steps behind it, and no placeholder left over.
      expect(rows()).toEqual([])
      expect(box.textContent).not.toContain('{{')
      expect(calls.filter((call) => call.path === '/api/oidc/authentik/setup').map((call) => call.body)).toEqual([
        { url: 'https://auth.example.com', token: 'token', confirm_issuer_change: false, public_url: '' },
      ])
    })

    it('runs the button once more with the confirmation on yes, and shows what that run did', async () => {
      answer = stopped
      afterConfirm = STEP_KEYS.map((key) => ({ key, ok: true, detail: key }))
      count = 2
      await card('en')
      await press(authentikCard(), i18n.t('oidc.authentik.run'))
      await settle()
      await press(dialog()!, i18n.t('oidc.authentik.run'))
      await settle()
      const bodies = calls.filter((call) => call.path === '/api/oidc/authentik/setup').map((call) => call.body)
      expect(bodies).toEqual([
        { url: 'https://auth.example.com', token: 'token', confirm_issuer_change: false, public_url: '' },
        { url: 'https://auth.example.com', token: 'token', confirm_issuer_change: true, public_url: '' },
      ])
      expect(dialog()).toBeNull()
      expect(rows()).toEqual(STEP_KEYS.map((key) => `✓${i18n.t(`oidc.authentik.step.${key}`)}`))
      expect(tokenField().value).toBe('')
    })

    it('changes nothing on no: no second call, the token stays for the next try', async () => {
      answer = stopped
      count = 2
      await card('en')
      await press(authentikCard(), i18n.t('oidc.authentik.run'))
      await settle()
      await press(dialog()!, i18n.t('oidc.admin.cancel'))
      await settle()
      expect(dialog()).toBeNull()
      expect(calls.filter((call) => call.path === '/api/oidc/authentik/setup')).toHaveLength(1)
      expect(tokenField().value).toBe('token')
    })

    it('does not ask for any other failure', async () => {
      answer = [{ key: STEP_KEYS[0], ok: false, detail: 'down', reason: 'unreachable', status: 0 }]
      await card('en')
      await press(authentikCard(), i18n.t('oidc.authentik.run'))
      await settle()
      expect(dialog()).toBeNull()
    })
  })

  describe('the public address in the card (module 1.0.10)', () => {
    const NONE = { ...ADDRESS, public_url: '', seen: 'http://localhost:3000', redirect_uri: 'http://localhost:3000/api/oidc/authentik/callback' }
    const dialog = () => document.querySelector('[role="dialog"]')
    const setups = () => calls.filter((call) => call.path === '/api/oidc/authentik/setup').map((call) => call.body)
    const saves = () => calls.filter((call) => call.path === '/api/oidc/authentik/address' && call.method === 'POST').map((call) => call.body)

    it('shows the ordinary button with an address, and no suggestion', async () => {
      await page()
      expect(box.querySelector('[data-testid="authentik-no-address"]')).toBeNull()
      expect(box.querySelector('[data-testid="authentik-stale"]')).toBeNull()
      expect(button(authentikCard(), i18n.t('oidc.authentik.run'))).toBeDefined()
      expect(button(authentikCard(), i18n.t('oidc.authentik.adoptOnly'))).toBeUndefined()
      expect(authentikCard().querySelector('a[href="/api/oidc/authentik/blueprint"]')).not.toBeNull()
    })

    it.each(['en', 'de'] as const)('suggests the address of the browser in %s, with where authentik leads back to, and sends it with the button', async (language) => {
      addressView = NONE
      answer = STEP_KEYS.map((key) => ({ key, ok: true, detail: key }))
      await card(language)
      const warning = box.querySelector('[data-testid="authentik-no-address"]')!
      expect(warning.textContent).toContain(i18n.t('oidc.authentik.noAddress'))
      expect(warning.querySelector('[data-testid="authentik-proposal"]')!.textContent).toBe('http://localhost:3000')
      expect(warning.textContent).toContain('http://localhost:3000/api/oidc/authentik/callback')
      expect(box.textContent).not.toContain('{{')
      // The blueprint has nothing to name yet: no link.
      expect(authentikCard().querySelector('a[href="/api/oidc/authentik/blueprint"]')).toBeNull()
      await press(authentikCard(), i18n.t('oidc.authentik.adopt'))
      await settle()
      expect(setups()).toEqual([{ url: 'https://auth.example.com', token: 'token', confirm_issuer_change: false, public_url: 'http://localhost:3000' }])
    })

    it('warns when the server sees another scheme than the browser uses, and not when they agree', async () => {
      addressView = { ...NONE, seen: 'http://localhost:3000' }
      await page()
      expect(box.querySelector('[data-testid="authentik-proxy-warning"]')).toBeNull()
      act(() => root.unmount())
      root = createRoot(box)
      addressView = { ...NONE, seen: 'https://localhost:3000' }
      await page()
      expect(box.querySelector('[data-testid="authentik-proxy-warning"]')!.textContent).toContain('https://localhost:3000')
      act(() => root.unmount())
      root = createRoot(box)
      addressView = { ...NONE, proxy_ignored: true }
      await page()
      expect(box.querySelector('[data-testid="authentik-proxy-warning"]')).not.toBeNull()
    })

    it('says that authentik still knows the old address, with the old one in it', async () => {
      addressView = { ...ADDRESS, stale: true, registered_redirect_uri: 'https://old.example.com/api/oidc/authentik/callback' }
      await page()
      expect(box.querySelector('[data-testid="authentik-stale"]')!.textContent).toBe(
        i18n.t('oidc.authentik.stale', { address: 'https://old.example.com/api/oidc/authentik/callback' }),
      )
    })

    it('saves only the address with the second button, asks first when that changes what authentik knows, and says yes with the confirmation', async () => {
      addressView = NONE
      refusal = { status: 409, detail: { code: 'public_url_change', message: 'x', count: 1, hand: 2, address: 'https://old.example.com/cb' } }
      await page()
      await press(authentikCard(), i18n.t('oidc.authentik.adoptOnly'))
      await settle()
      const asked = dialog()!.textContent!
      expect(asked).toContain(i18n.t('oidc.admin.addressChangeConfirm', { address: 'https://old.example.com/cb' }))
      expect(asked).toContain(i18n.t('oidc.admin.addressChangeHand', { count: 2 }))
      expect(saves()).toEqual([{ public_url: 'http://localhost:3000', confirm: false }])
      await press(dialog()!, i18n.t('oidc.authentik.adoptOnly'))
      await settle()
      expect(saves()).toEqual([
        { public_url: 'http://localhost:3000', confirm: false },
        { public_url: 'http://localhost:3000', confirm: true },
      ])
      expect(dialog()).toBeNull()
      expect(box.querySelector('[data-testid="authentik-no-address"]')).toBeNull()
    })

    it('asks about the entries by hand alone, without the sentence about authentik', async () => {
      addressView = NONE
      refusal = { status: 409, detail: { code: 'public_url_change', message: 'x', count: 0, hand: 1, address: '' } }
      await page()
      await press(authentikCard(), i18n.t('oidc.authentik.adoptOnly'))
      await settle()
      expect(dialog()!.textContent).toContain(i18n.t('oidc.admin.addressChangeHand', { count: 1 }))
      expect(dialog()!.textContent).not.toContain('authentik still knows')
    })

    it('says an address that is none cannot be used', async () => {
      addressView = NONE
      refusal = { status: 422, detail: { code: 'public_url_invalid', message: 'x' } }
      await page()
      await press(authentikCard(), i18n.t('oidc.authentik.adoptOnly'))
      await settle()
      expect(authentikCard().querySelector('[role="alert"]')!.textContent).toBe(i18n.t('oidc.authentik.why.unusable'))
      expect(dialog()).toBeNull()
    })

    it('asks in card 1 as well before the address is saved, and sends the confirmation on yes', async () => {
      refusal = { status: 409, detail: { code: 'public_url_change', message: 'x', count: 1, hand: 0, address: 'https://time.example.com/cb' } }
      await page()
      const card1 = document.getElementById('sign-in')!
      type(card1.querySelector(`input[placeholder="https://time.example.com"]`), 'https://new.example.com')
      await act(async () => card1.querySelector('form')!.requestSubmit())
      await settle()
      expect(dialog()!.textContent).toContain(i18n.t('oidc.admin.addressChangeConfirm', { address: 'https://time.example.com/cb' }))
      const puts = () => calls.filter((call) => call.method === 'PUT' && call.path === '/api/settings').map((call) => call.body)
      expect(puts()).toEqual([{ public_url: 'https://new.example.com', confirm_address: false }])
      await press(dialog()!, i18n.t('account.save'))
      await settle()
      expect(puts()).toEqual([
        { public_url: 'https://new.example.com', confirm_address: false },
        { public_url: 'https://new.example.com', confirm_address: true },
      ])
      expect(dialog()).toBeNull()
    })
  })
})
