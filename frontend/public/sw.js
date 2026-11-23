/*
 * The service worker of nextime.
 *
 * Notifications (TIME-21) and reminders (TIME-7): it shows what the server sent, and a tap opens what it points to, in
 * a window of nextime that is open already or in a new one. A reminder carries two buttons where the browser shows
 * them: "In 5 Min erneut" sends the mark the server put into it back (it is good for this browser and this reminder
 * only, TIME-7 EC-17), "Öffnen" opens the event as a tap does.
 *
 * The installable web app (TIME-28), built as nexnotes builds it (NOTES-23):
 *  - The shell. The build writes its own stamp and the list of its files into this file (vite.config.ts, plugin
 *    "service-worker"), so every build is a new worker. It keeps the start page and the built files, so the installed
 *    app opens without the network and reads what the page kept for it (IndexedDB, `features/offline`). Without a
 *    build (Vite's dev server) nothing is kept.
 *    - Pages (every navigation): the network first; the kept start page only without network, when the server is down
 *      (502, 503, 504) or after PAGE_WAIT. Only an answer the server marks as the start page (X-Nextime-Shell: 1) is
 *      kept.
 *    - Built files under /assets/ (their names carry a hash and never change): the kept one first.
 *    - The few files of the shell without a hash (symbols, manifest, theme.js, logo): the network first.
 *    - /api/..., /dav/..., /abo/... and every other address: never touched, never kept. Events never lie in this
 *      worker's cache.
 *    Only answers of the own origin, of type "basic", not redirected, 200, without "no-store", are kept. Older caches
 *    go when a new worker takes over; the first worker takes over at once, a later one when the page says so ("Neu
 *    laden", EC-7).
 *  - Signing out and another account in the tab send "nextime-forget": every entry of the cache that is not a file of
 *    the shell goes.
 */
'use strict'

const VERSION = '__NEXTIME_BUILD__'
const SHELL = /* __NEXTIME_SHELL__ */ []
const BUILT = !VERSION.startsWith('__')
const PREFIX = 'nextime-shell-'
const CACHE = PREFIX + VERSION
/** The start page, the same for every address of the app. */
const START = '/index.html'
/** How long a page may take before the kept start page shows. */
const PAGE_WAIT = 4000
/** The server's mark on the start page: the one answer to a navigation that may be kept. */
const SHELL_HEADER = 'x-nextime-shell'
/** Addresses never touched: the API, the calendar account, the guests' subscriptions. */
const NEVER = ['/api', '/dav', '/abo', '/.well-known']
/** A proxy's answers while the server is down. */
const DOWN = [502, 503, 504]

// ---- What happens to a request -----------------------------------------------------------------------------------------

/** `page`, `asset`, `file`, or `network` (not touched here). */
function strategy(request, origin, built) {
  const url = new URL(request.url)
  if (url.origin !== origin) return 'network'
  if (request.method !== 'GET') return 'network'
  if (NEVER.some((path) => url.pathname === path || url.pathname.startsWith(path + '/'))) return 'network'
  if (!built || url.pathname === '/sw.js') return 'network'
  if (request.mode === 'navigate') return 'page'
  if (url.pathname.startsWith('/assets/')) return 'asset'
  if (SHELL.includes(url.pathname)) return 'file'
  return 'network'
}

/** Whether an answer may be kept: the own origin's, whole, not redirected, not forbidden to keep. A page only when the
 * server marked it as the start page; a file never as a page (the start page the server gives for a missing file). */
function keepable(response, kind) {
  if (!response || response.status !== 200 || response.type !== 'basic' || response.redirected) return false
  if (/no-store/i.test(response.headers.get('cache-control') || '')) return false
  const type = response.headers.get('content-type') || ''
  if (kind === 'page') return response.headers.get(SHELL_HEADER) === '1' && type.includes('text/html')
  return !type.includes('text/html')
}

/** Whether an entry of the cache belongs to the shell; anything else goes when the account signs out. */
function shellEntry(url) {
  const path = new URL(url).pathname
  return path === START || path.startsWith('/assets/') || SHELL.includes(path)
}

async function keep(key, response, kind) {
  if (!keepable(response, kind)) return false
  const cache = await caches.open(CACHE)
  await cache.put(key, response)
  return true
}

/** Waits `ms`, then gives `value`; never when `ms` is null. */
function after(ms, value) {
  return ms === null ? new Promise(() => {}) : new Promise((resolve) => setTimeout(() => resolve(value), ms))
}

async function page(request) {
  const kept = await caches.match(START)
  const network = fetch(request).then(async (response) => {
    await keep(START, response.clone(), 'page').catch(() => false)
    return response
  })
  if (!kept) return network
  const answer = network.then((response) => (DOWN.includes(response.status) ? kept : response)).catch(() => kept)
  return Promise.race([answer, after(PAGE_WAIT, kept)])
}

async function asset(request) {
  const kept = await caches.match(request)
  if (kept) return kept
  const response = await fetch(request)
  await keep(request, response.clone(), 'asset').catch(() => false)
  return response
}

async function file(request) {
  try {
    const response = await fetch(request)
    await keep(request, response.clone(), 'file').catch(() => false)
    return response
  } catch (error) {
    const kept = await caches.match(request, { ignoreSearch: true })
    if (kept) return kept
    throw error
  }
}

/** Every entry of the cache outside the shell goes (signing out, another account). */
async function forgetForeign() {
  for (const name of await caches.keys()) {
    if (!name.startsWith(PREFIX)) continue
    const cache = await caches.open(name)
    for (const request of await cache.keys()) if (!shellEntry(request.url)) await cache.delete(request)
  }
}

// ---- Events ----------------------------------------------------------------------------------------------------------

self.addEventListener('fetch', (event) => {
  const how = strategy(event.request, self.location.origin, BUILT)
  if (how === 'network') return
  event.respondWith(how === 'page' ? page(event.request) : how === 'asset' ? asset(event.request) : file(event.request))
})

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      if (BUILT) {
        // One by one and checked like every other answer: a proxy that sends the sign-in of another site instead of
        // the start page leaves the cache empty, never wrong.
        await Promise.allSettled(
          [START, ...SHELL].map(async (path) => {
            const response = await fetch(new Request(path, { cache: 'reload' }))
            await keep(path, response, path === START ? 'page' : path.startsWith('/assets/') ? 'asset' : 'file')
          }),
        )
      }
      // The first worker takes over at once (also the one of before, which only showed notifications); a new one
      // waits until the page says "reload" (or every tab is closed), so a page open while writing never loses the
      // files of its own build.
      if (!self.registration.active || !BUILT) await self.skipWaiting()
    })(),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys()
      await Promise.all(names.filter((name) => name.startsWith(PREFIX) && name !== CACHE).map((name) => caches.delete(name)))
      await self.clients.claim()
    })(),
  )
})

self.addEventListener('message', (event) => {
  const data = event.data || {}
  const port = event.ports && event.ports[0]
  if (data.type === 'nextime-update') return void self.skipWaiting()
  // Which build this worker holds: the page takes it over silently when it is its own (a hard reload passed it by).
  if (data.type === 'nextime-shell' && port) return void port.postMessage({ version: VERSION, shell: SHELL })
  if (data.type === 'nextime-forget' && event.waitUntil) event.waitUntil(forgetForeign())
})

// ---- Notifications ---------------------------------------------------------------------------------------------------

/** Only an address inside nextime, never another site. */
function inside(url) {
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') ? url : '/'
}

/** At most two buttons, each with an action nextime knows and a title of text. */
function actionsOf(data) {
  if (!Array.isArray(data.actions)) return []
  return data.actions
    .filter((each) => each && (each.action === 'snooze' || each.action === 'open') && typeof each.title === 'string')
    .slice(0, 2)
    .map((each) => ({ action: each.action, title: each.title }))
}

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = {}
  }
  const title = typeof data.title === 'string' && data.title ? data.title : 'nextime'
  const options = {
    body: typeof data.body === 'string' ? data.body : '',
    tag: typeof data.tag === 'string' ? data.tag : undefined,
    icon: '/logo.svg',
    data: { url: inside(data.url), snooze: typeof data.snooze === 'string' ? data.snooze : '' },
  }
  const actions = actionsOf(data)
  if (actions.length && typeof Notification !== 'undefined' && (Notification.maxActions || 0) > 0) options.actions = actions
  event.waitUntil(self.registration.showNotification(title, options))
})

async function again(mark) {
  try {
    await fetch('/api/reminders/snooze', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Nextime-Client': 'service-worker' },
      body: JSON.stringify({ mark }),
    })
  } catch {
    // Without a connection the reminder does not come again; the notification is gone all the same.
  }
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const data = event.notification.data || {}
  if (event.action === 'snooze' && data.snooze) {
    event.waitUntil(again(data.snooze))
    return
  }
  const path = inside(data.url)
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const client of windows) {
        if (new URL(client.url).origin !== self.location.origin) continue
        await client.focus()
        client.postMessage({ type: 'nextime-open', url: path })
        return
      }
      await self.clients.openWindow(path)
    })(),
  )
})
