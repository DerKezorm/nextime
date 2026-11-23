/**
 * Whether the server answers (TIME-28 AC-3, AC-4, EC-4). The page counts as without a connection when the browser says
 * it lost its network, or when `GET /api/health` does not answer within `SLOW_MS`. A request that fails without the
 * server (no network, a proxy saying it is down: 502, 503, 504) or a view that takes longer than `SLOW_MS` only makes
 * the page ask `/api/health` at once: a slow server that still answers is no reason to go "offline" (review of TIME-28,
 * finding 2: no flapping between the two, no load of every view asking again every few seconds).
 *
 * Without a connection the page asks `/api/health` again after 5, 10 and then every 30 seconds, and at once when the
 * browser says the network is back. The first answer ends it, and every view asks again once (`announce`).
 */
import { useSyncExternalStore } from 'react'

import { ApiError } from '../../api/client'

/** How long the server may take before the page asks whether it is there at all (EC-4). */
export const SLOW_MS = 8_000
/** How long the page waits between two questions while without a connection: growing, then the last one each time. */
export const PROBE_STEPS = [5_000, 10_000, 30_000]

let offline = false
const listeners = new Set<() => void>()
const backListeners = new Set<() => void>()
let timer: number | null = null
let step = 0
let checking: Promise<void> | null = null

function tell(): void {
  for (const listener of [...listeners]) listener()
}

/** No server, as far as `error` tells: the network is gone, a proxy says the server is down, or a time limit ran out.
 * A request the page stopped itself (`AbortError`) is none of these. */
export function unreachableError(error: unknown): boolean {
  if (error instanceof ApiError) return error.status === 502 || error.status === 503 || error.status === 504
  return error instanceof TypeError || (error instanceof DOMException && error.name === 'TimeoutError')
}

async function probe(): Promise<boolean> {
  const stop = typeof AbortController === 'function' ? new AbortController() : null
  const late = window.setTimeout(() => stop?.abort(), SLOW_MS)
  try {
    const answer = await fetch('/api/health', { signal: stop?.signal, cache: 'no-store' })
    return answer.ok
  } catch {
    return false
  } finally {
    window.clearTimeout(late)
  }
}

function schedule(): void {
  if (timer !== null || typeof window === 'undefined') return
  timer = window.setTimeout(() => {
    timer = null
    if (!offline) return
    void probe().then((answered) => {
      if (answered) markReachable()
      else {
        step = Math.min(step + 1, PROBE_STEPS.length - 1)
        schedule()
      }
    })
  }, PROBE_STEPS[step])
}

function stopProbing(): void {
  if (timer !== null) window.clearTimeout(timer)
  timer = null
}

/** The server did not answer (or the browser lost its network): read only, from what is kept. */
export function markUnreachable(): void {
  if (offline) return
  offline = true
  step = 0
  schedule()
  tell()
}

/** The server answered: everything works again, and whoever waits for that hears it. */
export function markReachable(): void {
  if (!offline) return
  offline = false
  stopProbing()
  tell()
  for (const listener of [...backListeners]) listener()
}

/** Something looks like no server: `/api/health` decides, once at a time. */
export function suspect(): Promise<void> {
  if (offline) return Promise.resolve()
  if (!checking) {
    checking = probe()
      .then((answered) => {
        if (!answered) markUnreachable()
      })
      .finally(() => {
        checking = null
      })
  }
  return checking
}

/** A request failed: whether that may mean the server is gone (the health question decides). */
export function noteFailure(error: unknown): void {
  if (unreachableError(error)) void suspect()
}

export function isOffline(): boolean {
  return offline
}

/** `listener` runs whenever the connection comes back. */
export function onReconnect(listener: () => void): () => void {
  backListeners.add(listener)
  return () => backListeners.delete(listener)
}

export function useOffline(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => offline,
    () => offline,
  )
}

/** A request with an eye on the time: one that takes longer than `ms` makes the page ask whether the server is there
 * (EC-4); its answer still counts when it comes, and an answer means the server is there. */
export function watchSlow<T>(promise: Promise<T>, ms = SLOW_MS): Promise<T> {
  const late = window.setTimeout(() => void suspect(), ms)
  return promise.then(
    (value) => {
      window.clearTimeout(late)
      markReachable()
      return value
    },
    (error) => {
      window.clearTimeout(late)
      noteFailure(error)
      throw error
    },
  )
}

async function back(): Promise<void> {
  if (offline && (await probe())) markReachable()
}

if (typeof window !== 'undefined') {
  window.addEventListener('offline', markUnreachable)
  window.addEventListener('online', () => void back())
}

/** Whether the page asks `/api/health` on its own now (for the tests). */
export function probing(): boolean {
  return timer !== null
}

/** For the tests: as after a fresh page load. */
export function resetConnection(): void {
  offline = false
  step = 0
  checking = null
  stopProbing()
  tell()
}
