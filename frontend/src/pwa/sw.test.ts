/**
 * TIME-28: the service worker (`public/sw.js`) as it runs, in a small stand-in of a worker's world, as nexnotes tests
 * its own (NOTES-23): what happens to a request, what may be kept, what goes when the account signs out, and that a new
 * build waits for "Neu laden" while the first takes over at once (EC-7). The file is read as it ships; the build's marks
 * are filled in as the build does (`vite.config.ts`). Push and reminders keep working (TIME-7, TIME-21).
 */
type Files = { readFileSync: (file: string, encoding: string) => string }
const fsName = 'node:fs'
const files = (await import(/* @vite-ignore */ fsName)) as Files
const SOURCE = files.readFileSync('public/sw.js', 'utf-8')
const CONFIG = files.readFileSync('vite.config.ts', 'utf-8')
const MANIFEST = JSON.parse(files.readFileSync('public/manifest.webmanifest', 'utf-8')) as Record<string, unknown>
const INDEX = files.readFileSync('index.html', 'utf-8')

const ORIGIN = 'https://nextime.example.com'

type FakeResponse = { status: number; type: string; redirected: boolean; headers: Headers; body: string; clone: () => FakeResponse }

function answer(body: string, init: { status?: number; type?: string; redirected?: boolean; headers?: Record<string, string> } = {}): FakeResponse {
  return {
    status: init.status ?? 200,
    type: init.type ?? 'basic',
    redirected: init.redirected ?? false,
    headers: new Headers(init.headers ?? {}),
    body,
    clone(this: FakeResponse) {
      return { ...this }
    },
  }
}

const shell = (body = 'shell') => answer(body, { headers: { 'content-type': 'text/html; charset=utf-8', 'x-nextime-shell': '1' } })
const script = (body = 'js') => answer(body, { headers: { 'content-type': 'text/javascript' } })

function keyOf(request: string | { url: string }, ignoreSearch = false): string {
  const url = new URL(typeof request === 'string' ? request : request.url, ORIGIN)
  if (ignoreSearch) url.search = ''
  return url.href
}

function fakeCaches() {
  const stores = new Map<string, Map<string, FakeResponse>>()
  const open = async (name: string) => {
    if (!stores.has(name)) stores.set(name, new Map())
    const store = stores.get(name)!
    return {
      put: async (request: string | { url: string }, response: FakeResponse) => void store.set(keyOf(request), response),
      keys: async () => [...store.keys()].map((url) => ({ url })),
      delete: async (request: { url: string }) => store.delete(keyOf(request)),
      match: async (request: string | { url: string }, options: { ignoreSearch?: boolean } = {}) => {
        for (const [key, value] of store) if (keyOf(key, options.ignoreSearch) === keyOf(request, options.ignoreSearch)) return value
        return undefined
      },
    }
  }
  return {
    stores,
    open,
    keys: async () => [...stores.keys()],
    delete: async (name: string) => stores.delete(name),
    match: async (request: string | { url: string }, options: { ignoreSearch?: boolean } = {}) => {
      for (const name of stores.keys()) {
        const found = await (await open(name)).match(request, options)
        if (found) return found
      }
      return undefined
    },
    all: () => [...stores.values()].flatMap((store) => [...store.keys()]),
  }
}

type Listener = (event: Record<string, unknown>) => void

function load({ built = true, shellFiles = ['/logo.svg', '/manifest.webmanifest', '/assets/index-abc.js'], version = 'b1', active = null as unknown } = {}) {
  let code = SOURCE
  if (built) code = code.replace("'__NEXTIME_BUILD__'", JSON.stringify(version)).replace('/* __NEXTIME_SHELL__ */ []', JSON.stringify(shellFiles))
  const listeners: Record<string, Listener> = {}
  const shown: { title: string; options: Record<string, unknown> }[] = []
  const self = {
    addEventListener: (type: string, listener: Listener) => void (listeners[type] = listener),
    location: { origin: ORIGIN },
    registration: { active, showNotification: vi.fn(async (title: string, options: Record<string, unknown>) => void shown.push({ title, options })) },
    skipWaiting: vi.fn(async () => undefined),
    clients: { claim: vi.fn(async () => undefined), matchAll: vi.fn(async () => []), openWindow: vi.fn(async () => undefined) },
  }
  const caches = fakeCaches()
  const fetch = vi.fn(async (_input?: unknown): Promise<FakeResponse> => {
    throw new TypeError('Failed to fetch')
  })
  const RequestLike = function (this: { url: string }, url: string) {
    this.url = new URL(url, ORIGIN).href
  } as unknown as typeof Request
  const api = new Function('self', 'caches', 'fetch', 'Request', 'Notification', code + '\nreturn { strategy, keepable, shellEntry, CACHE }')(
    self,
    caches,
    fetch,
    RequestLike,
    { maxActions: 2 },
  ) as {
    strategy: (request: { url: string; method: string; mode?: string }, origin: string, built: boolean) => string
    keepable: (response: FakeResponse | null, kind: string) => boolean
    shellEntry: (url: string) => boolean
    CACHE: string
  }
  return { api, listeners, caches, fetch, self, shown }
}

type World = ReturnType<typeof load>

const request = (path: string, init: { method?: string; mode?: string; origin?: string } = {}) => ({
  url: (init.origin ?? ORIGIN) + path,
  method: init.method ?? 'GET',
  mode: init.mode ?? 'cors',
})

function fetchEvent(world: World, req: Record<string, unknown>) {
  let answered: Promise<unknown> | null = null
  world.listeners.fetch({ request: req, respondWith: (promise: Promise<unknown>) => void (answered = Promise.resolve(promise)) })
  return answered as Promise<unknown> | null
}

async function lifecycle(world: World, type: 'install' | 'activate') {
  const waits: Promise<unknown>[] = []
  world.listeners[type]({ waitUntil: (promise: Promise<unknown>) => void waits.push(promise) })
  await Promise.all(waits)
}

async function message(world: World, data: Record<string, unknown>): Promise<unknown> {
  let reply: unknown = undefined
  const waits: Promise<unknown>[] = []
  world.listeners.message({ data, ports: [{ postMessage: (value: unknown) => void (reply = value) }], waitUntil: (promise: Promise<unknown>) => void waits.push(promise) })
  await Promise.all(waits)
  return reply
}

describe('TIME-28 the installable web app', () => {
  it('TIME-28 AC-2 the start page names the manifest and the symbols; the manifest makes it an app with its own symbol', () => {
    expect(INDEX).toContain('<link rel="manifest" href="/manifest.webmanifest" />')
    expect(INDEX).toContain('rel="apple-touch-icon" href="/icons/apple-touch-icon.png')
    expect(INDEX).toContain('name="apple-mobile-web-app-capable" content="yes"')
    expect(MANIFEST.display).toBe('standalone')
    expect(MANIFEST.name).toBe('nextime')
    expect(MANIFEST.start_url).toBe('/')
    const icons = MANIFEST.icons as { src: string; sizes: string; purpose: string }[]
    expect(icons.map((icon) => `${icon.sizes} ${icon.purpose}`).sort()).toEqual(['192x192 any', '192x192 maskable', '512x512 any', '512x512 maskable'])
    for (const icon of icons) {
      // Each symbol is there, and the worker keeps it with the shell.
      const path = icon.src.split('?')[0]
      expect(files.readFileSync(`public${path}`, 'binary').startsWith('\x89PNG')).toBe(true)
      expect(CONFIG).toContain(`'${path}'`)
    }
  })

  it('TIME-28 AC-2 EC-7 pages, built files and shell files are the worker\'s; the API, the calendar account, guests and other sites never', () => {
    const { api } = load()
    const at = (path: string, init?: Parameters<typeof request>[1]) => api.strategy(request(path, init), ORIGIN, true)
    expect(at('/week', { mode: 'navigate' })).toBe('page')
    expect(at('/', { mode: 'navigate' })).toBe('page')
    expect(at('/assets/index-abc.js')).toBe('asset')
    expect(at('/logo.svg')).toBe('file')
    expect(at('/manifest.webmanifest')).toBe('file')
    for (const path of ['/api/events', '/api/offline', '/api', '/dav/calendars/x/', '/abo/token.ics', '/.well-known/caldav', '/sw.js', '/somewhere.png'])
      expect(at(path), path).toBe('network')
    expect(at('/api/events', { mode: 'navigate' })).toBe('network')
    expect(at('/abo/token', { mode: 'navigate' })).toBe('network')
    expect(at('/week', { mode: 'navigate', origin: 'https://other.example' })).toBe('network')
    expect(at('/api/events', { method: 'POST' })).toBe('network')
    // Without a build (the dev server) nothing is touched.
    expect(load({ built: false }).api.strategy(request('/week', { mode: 'navigate' }), ORIGIN, false)).toBe('network')
  })

  it('TIME-28 AC-2 only whole answers of the own origin are kept, a page only with the mark of the start page', () => {
    const { api } = load()
    expect(api.keepable(shell(), 'page')).toBe(true)
    expect(api.keepable(answer('<h1>x</h1>', { headers: { 'content-type': 'text/html' } }), 'page')).toBe(false)
    expect(api.keepable({ ...shell(), redirected: true }, 'page')).toBe(false)
    expect(api.keepable({ ...shell(), type: 'opaque' }, 'page')).toBe(false)
    expect(api.keepable(answer('x', { status: 503, headers: { 'x-nextime-shell': '1', 'content-type': 'text/html' } }), 'page')).toBe(false)
    expect(api.keepable(answer('{}', { headers: { 'content-type': 'application/json', 'cache-control': 'no-store' } }), 'file')).toBe(false)
    expect(api.keepable(script(), 'asset')).toBe(true)
    // The start page the server gives for a missing file is never kept as that file.
    expect(api.keepable(shell(), 'asset')).toBe(false)
  })

  it('TIME-28 AC-3 EC-7 without the network the kept start page answers; with it the page comes from the server (a new build)', async () => {
    const world = load()
    world.fetch.mockImplementation(async (input: unknown) => {
      const path = new URL(typeof input === 'string' ? input : (input as { url: string }).url, ORIGIN).pathname
      return path === '/index.html' ? shell('old build') : path.startsWith('/assets/') ? script() : answer('x', { headers: { 'content-type': 'image/svg+xml' } })
    })
    await lifecycle(world, 'install')
    expect(world.caches.all()).toContain(`${ORIGIN}/index.html`)
    expect(world.caches.all()).toContain(`${ORIGIN}/assets/index-abc.js`)
    // With the network: the server's page (here a new build).
    world.fetch.mockImplementation(async () => shell('new build'))
    expect(((await fetchEvent(world, request('/week', { mode: 'navigate' }))) as FakeResponse).body).toBe('new build')
    // Without: the kept one.
    world.fetch.mockImplementation(async () => {
      throw new TypeError('Failed to fetch')
    })
    expect(((await fetchEvent(world, request('/week', { mode: 'navigate' }))) as FakeResponse).body).toBe('new build')
    // The server down behind a proxy: the kept one too.
    world.fetch.mockImplementation(async () => answer('bad gateway', { status: 502, headers: { 'content-type': 'text/html' } }))
    expect(((await fetchEvent(world, request('/', { mode: 'navigate' }))) as FakeResponse).body).toBe('new build')
  })

  it('TIME-28 EC-7 the first worker takes over at once, a new build waits for "Neu laden"; old caches go', async () => {
    const first = load({ version: 'b1' })
    await lifecycle(first, 'install')
    expect(first.self.skipWaiting).toHaveBeenCalledTimes(1)
    const next = load({ version: 'b2', active: {} })
    next.caches.stores.set('nextime-shell-b1', new Map())
    next.caches.stores.set('other-cache', new Map())
    await lifecycle(next, 'install')
    expect(next.self.skipWaiting).not.toHaveBeenCalled()
    await message(next, { type: 'nextime-update' })
    expect(next.self.skipWaiting).toHaveBeenCalledTimes(1)
    await lifecycle(next, 'activate')
    expect([...next.caches.stores.keys()]).not.toContain('nextime-shell-b1')
    expect([...next.caches.stores.keys()]).toContain('other-cache')
    expect(await message(next, { type: 'nextime-shell' })).toEqual({ version: 'b2', shell: ['/logo.svg', '/manifest.webmanifest', '/assets/index-abc.js'] })
  })

  it('TIME-28 EC-1 signing out makes the worker forget everything that is not the shell', async () => {
    const world = load()
    const cache = new Map<string, FakeResponse>([
      [`${ORIGIN}/index.html`, shell()],
      [`${ORIGIN}/assets/index-abc.js`, script()],
      [`${ORIGIN}/somewhere/else`, script()],
    ])
    world.caches.stores.set(world.api.CACHE, cache)
    await message(world, { type: 'nextime-forget' })
    expect(world.caches.all().sort()).toEqual([`${ORIGIN}/assets/index-abc.js`, `${ORIGIN}/index.html`])
  })

  it('TIME-28 AC-2 the same worker still shows notifications and reminders (TIME-7, TIME-21)', async () => {
    const world = load()
    let waited: Promise<unknown> = Promise.resolve()
    world.listeners.push({ data: { json: () => ({ title: 'Zahnarzt', body: '16:30', url: '//evil.example', actions: [{ action: 'snooze', title: 'In 5 Min erneut' }] }) }, waitUntil: (promise: Promise<unknown>) => (waited = promise) })
    await waited
    expect(world.shown[0].title).toBe('Zahnarzt')
    expect((world.shown[0].options.data as { url: string }).url).toBe('/')
    expect(world.shown[0].options.actions).toEqual([{ action: 'snooze', title: 'In 5 Min erneut' }])
  })
})
