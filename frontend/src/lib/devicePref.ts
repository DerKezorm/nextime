/**
 * Choices of the look that belong to this device and the account signed in on it, never to the server: how wide the
 * columns are, how wide the text runs, whether the outline shows. Kept in `localStorage` under
 * `nextime.<name>.<account>`; a blocked or full storage keeps the choice until the page is reloaded.
 */
import { useSyncExternalStore } from 'react'

import { useAccountId } from '../state/auth'

const listeners = new Set<() => void>()
/** What was read or set in this page load, so every reader sees the same at once. */
const cache = new Map<string, string | null>()

export function prefKey(name: string, account: number): string {
  return `nextime.${name}.${account}`
}

function read(key: string): string | null {
  if (cache.has(key)) return cache.get(key) ?? null
  let value: string | null
  try {
    value = localStorage.getItem(key)
  } catch {
    value = null
  }
  cache.set(key, value)
  return value
}

export function setPref(name: string, account: number, value: string | null): void {
  const key = prefKey(name, account)
  cache.set(key, value)
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Holds until the page is reloaded.
  }
  for (const listener of [...listeners]) listener()
}

const signOutNames = new Set<string>()

/** `name` ends with the session: signing out on this device takes it away (TIME-13 EC-17, "only this one"). */
export function forgetOnSignOut(name: string): void {
  signOutNames.add(name)
}

/** The account signed out on this device: what lasts only as long as its session goes. */
export function signedOut(account: number): void {
  for (const name of signOutNames) setPref(name, account, null)
}

/** For tests: as after a fresh page load. */
export function forgetPrefs(): void {
  cache.clear()
}

/**
 * The stored value of `name` for the account signed in, read through `parse` (which says what a missing or broken
 * value means); and how to change it (`null` goes back to the default).
 */
export function useDevicePref<T>(name: string, parse: (raw: string | null) => T): [T, (value: string | null) => void] {
  const account = useAccountId() ?? 0
  const raw = useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => read(prefKey(name, account)),
  )
  return [parse(raw), (value) => setPref(name, account, value)]
}
