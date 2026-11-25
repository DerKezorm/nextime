/**
 * nextime as an installed app (TIME-28, as nexnotes NOTES-23): the service worker (`public/sw.js`) is signed up at the start, a new one
 * waiting is offered as "New version, reload", and the page talks to it (forgetting the account).
 * Whether the app runs installed (`display-mode: standalone`) is known here too.
 */
import { useSyncExternalStore } from 'react'

import { worker } from '../features/notify/browser'

type State = {
  /** A new version of the app waits; reloading takes it. */
  update: boolean
  /** Runs as the installed app. */
  standalone: boolean
  /** The browser offered its own question to install (`beforeinstallprompt`). */
  installable: boolean
  /** An iPhone or iPad: installing goes through Share › Add to Home Screen. */
  ios: boolean
}

/** What a browser hands over with `beforeinstallprompt`. */
type InstallOffer = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> }

let state: State = { update: false, standalone: false, installable: false, ios: false }
let offer: InstallOffer | null = null
let waiting: ServiceWorker | null = null
const listeners = new Set<() => void>()

function set(next: Partial<State>): void {
  state = { ...state, ...next }
  for (const listener of listeners) listener()
}

export function workerSupported(): boolean {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator && typeof window !== 'undefined' && window.isSecureContext !== false
}

export function isStandalone(): boolean {
  try {
    if (window.matchMedia('(display-mode: standalone)').matches) return true
  } catch {
    // An old browser without matchMedia: not installed.
  }
  return (navigator as Navigator & { standalone?: boolean }).standalone === true
}

/** The page's own entry script, as the built start page names it (`/assets/index-<hash>.js`). */
function entryScript(doc: Document = document): string | null {
  return doc.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')?.getAttribute('src') ?? null
}

/** Whether a worker's build is the one this page already runs: its files name the page's own entry script. */
export function isCurrentBuild(shell: unknown, entry: string | null = entryScript()): boolean {
  if (!entry || !Array.isArray(shell)) return false
  const path = entry.startsWith('/') ? entry : new URL(entry, window.location.origin).pathname
  return shell.includes(path)
}

/** Asks a worker and waits for its answer over a MessageChannel; `null` when none comes within `ms`. */
export function ask<T>(target: ServiceWorker | null | undefined, message: Record<string, unknown>, ms = 3000): Promise<T | null> {
  if (!target) return Promise.resolve(null)
  return new Promise((resolve) => {
    const channel = new MessageChannel()
    const late = window.setTimeout(() => resolve(null), ms)
    channel.port1.onmessage = (event) => {
      window.clearTimeout(late)
      resolve((event.data as T | null) ?? null)
    }
    target.postMessage(message, [channel.port2])
  })
}

/** The worker that answers this page: the one in control, else the active one. */
export async function activeWorker(): Promise<ServiceWorker | null> {
  if (!workerSupported()) return null
  if (navigator.serviceWorker.controller) return navigator.serviceWorker.controller
  const registration = await navigator.serviceWorker.getRegistration('/').catch(() => undefined)
  return registration?.active ?? null
}

/** Sends a message to every worker of the page (active and waiting); nothing when there is none. */
export async function tell(message: Record<string, unknown>): Promise<void> {
  if (!workerSupported()) return
  const registration = await navigator.serviceWorker.getRegistration('/').catch(() => undefined)
  const targets = new Set<ServiceWorker>()
  if (navigator.serviceWorker.controller) targets.add(navigator.serviceWorker.controller)
  for (const one of [registration?.active, registration?.waiting, registration?.installing]) if (one) targets.add(one)
  for (const target of targets) target.postMessage(message)
}

/** Set when a waiting worker turned out to be this page's own build: it takes over without a reload. */
let silent = false

function watch(registration: ServiceWorkerRegistration): void {
  const offerIfWaiting = async () => {
    // Only a worker that replaces one: the very first takes over by itself.
    const next = registration.waiting
    if (!next || !navigator.serviceWorker.controller) return
    const answer = await ask<{ shell?: unknown }>(next, { type: 'nextime-shell' }, 1000)
    if (isCurrentBuild(answer?.shell)) {
      silent = true
      next.postMessage({ type: 'nextime-update' })
      return
    }
    waiting = next
    set({ update: true })
  }
  void offerIfWaiting()
  registration.addEventListener('updatefound', () => {
    const installing = registration.installing
    installing?.addEventListener('statechange', () => {
      if (installing.state === 'installed') void offerIfWaiting()
    })
  })
  // An app open for days still learns of a new version: on every return to it.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') void registration.update().catch(() => undefined)
  })
}

let started = false

/** Once, at the start (a built page only: the dev server has no shell to keep). */
export function startWorker(): void {
  if (started) return
  started = true
  set({ standalone: isStandalone() })
  try {
    window.matchMedia('(display-mode: standalone)').addEventListener('change', () => set({ standalone: isStandalone() }))
  } catch {
    // Stays as it was at the start.
  }
  if (!workerSupported()) return
  let reloading = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (silent) {
      silent = false
      return
    }
    // The new worker took over after "reload": now the page comes from it.
    if (!reloading && waiting) {
      reloading = true
      window.location.reload()
    }
  })
  worker()
    .then(watch)
    .catch(() => undefined)
}

export function isIos(agent = navigator.userAgent, touchPoints = navigator.maxTouchPoints ?? 0): boolean {
  // iPadOS asks for the desktop page and says "Macintosh"; it is the Mac with a touch screen.
  return /iPhone|iPad|iPod/.test(agent) || (/Macintosh/.test(agent) && touchPoints > 1)
}

/** The browser's offer to install, kept from the start of the page (it comes once, early). For the card "Auf diesem
 * Gerät installieren" (plan "Einstellungen", E6, E28). */
export function listenForInstall(): void {
  if (typeof window === 'undefined') return
  set({ standalone: isStandalone(), ios: isIos() })
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault()
    offer = event as InstallOffer
    set({ installable: true })
  })
  window.addEventListener('appinstalled', () => {
    offer = null
    set({ installable: false, standalone: isStandalone() })
  })
}

/** The browser's own question to install; true when it was accepted. */
export async function install(): Promise<boolean> {
  if (!offer) return false
  const asked = offer
  offer = null
  set({ installable: false })
  await asked.prompt()
  const { outcome } = await asked.userChoice
  return outcome === 'accepted'
}

/** Takes the waiting version: the worker takes over, the page reloads. */
export function takeUpdate(): void {
  if (waiting) waiting.postMessage({ type: 'nextime-update' })
  else window.location.reload()
}

export function usePwa(): State {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => state,
    () => state,
  )
}

/** For the tests. */
export function setPwaState(next: Partial<State>): void {
  set(next)
}

/** For the tests: the next start signs up again. */
export function forgetStarted(): void {
  started = false
  waiting = null
  silent = false
}
