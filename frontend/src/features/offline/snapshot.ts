/**
 * What the installed web app keeps for reading without a connection (TIME-28 AC-3): the occurrences of a week back and
 * four weeks ahead as `GET /api/offline` gave them (never more than the member may see: "busy only" comes as a block
 * without title, place or notes), the calendars, the collections and the own profile, under the number of the account
 * that took it. In IndexedDB (`nextime`, store `offline`, one entry), only in the installed app (outside of it the
 * spec leaves reading without a connection out).
 *
 * Signing out and another account in the tab forget it before anything of the next account is drawn (EC-1); an entry
 * of another account is never shown. A browser that cannot keep it (no IndexedDB, too little room) goes on without
 * reading offline, and says so once (EC-2).
 */
import { useSyncExternalStore } from 'react'

import type { Me } from '../../api/client'
import { currentAccount, onAccountChange } from '../../state/account'
import type { CalendarList, Collection, Occurrence } from '../calendars/api'

export type KeptOccurrence = Occurrence & { notes?: string }

export type Snapshot = {
  account: number
  /** When it was taken, on the device's clock (ms). */
  takenAt: number
  /** The window kept, as instants (UTC). */
  start: string
  end: string
  me: Me
  calendars: CalendarList
  collections: Collection[]
  occurrences: KeptOccurrence[]
}

/** Where the snapshot lies: IndexedDB in the browser, a stand-in in the tests. */
export type Shelf = {
  get: () => Promise<unknown>
  put: (value: Snapshot) => Promise<void>
  clear: () => Promise<void>
}

const DB = 'nextime'
const STORE = 'offline'
const KEY = 'snapshot'

function opened(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB, 1)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE)
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('indexedDB'))
    request.onblocked = () => reject(new Error('indexedDB blocked'))
  })
}

async function step<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await opened()
  try {
    return await new Promise<T>((resolve, reject) => {
      const transaction = db.transaction(STORE, mode)
      const request = run(transaction.objectStore(STORE))
      transaction.oncomplete = () => resolve(request.result)
      transaction.onerror = () => reject(transaction.error ?? request.error ?? new Error('indexedDB'))
      transaction.onabort = () => reject(transaction.error ?? new Error('indexedDB aborted'))
    })
  } finally {
    db.close()
  }
}

export const indexedShelf: Shelf = {
  get: () => step('readonly', (store) => store.get(KEY)),
  put: async (value) => void (await step('readwrite', (store) => store.put(value, KEY))),
  clear: async () => void (await step('readwrite', (store) => store.delete(KEY))),
}

function available(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB !== null
  } catch {
    return false
  }
}

let shelf: Shelf | null = available() ? indexedShelf : null
let current: Snapshot | null = null
/** Keeping failed on this device (EC-2); said once per page load. */
let failed = false
const listeners = new Set<() => void>()

function tell(): void {
  for (const listener of [...listeners]) listener()
}

function valid(value: unknown, account: number | null): value is Snapshot {
  if (!value || typeof value !== 'object') return false
  const kept = value as Partial<Snapshot>
  return (
    typeof kept.account === 'number' &&
    (account === null || kept.account === account) &&
    typeof kept.takenAt === 'number' &&
    typeof kept.start === 'string' &&
    typeof kept.end === 'string' &&
    Array.isArray(kept.occurrences) &&
    !!kept.me &&
    kept.me.id === kept.account &&
    !!kept.calendars &&
    Array.isArray(kept.calendars.calendars)
  )
}

/** What is kept, as far as it belongs to `account` (`null`: whoever took it, for a start without a connection). */
export async function readKept(account: number | null = null): Promise<Snapshot | null> {
  if (current && (account === null || current.account === account)) return current
  if (!shelf) return null
  try {
    const value = await shelf.get()
    if (!valid(value, account)) return null
    // The tab signed in as someone else meanwhile: not this one's to show.
    const now = currentAccount()
    if (now !== null && now !== value.account) return null
    current = value
    tell()
    return value
  } catch {
    return null
  }
}

/** Keeps `snapshot` for its account; whether it worked. */
export async function keep(snapshot: Snapshot): Promise<boolean> {
  if (currentAccount() !== snapshot.account) return false
  if (!shelf) {
    failed = true
    tell()
    return false
  }
  try {
    await shelf.put(snapshot)
  } catch {
    // Too little room, or the browser refuses: nextime goes on, only without reading offline.
    failed = true
    current = null
    tell()
    return false
  }
  // Signed out or another account while it was being written: it goes again at once.
  if (currentAccount() !== snapshot.account) {
    await forgetKept()
    return false
  }
  current = snapshot
  failed = false
  tell()
  return true
}

/** Forgets what is kept, in memory and on the device. */
export async function forgetKept(): Promise<void> {
  current = null
  tell()
  if (!shelf) return
  try {
    await shelf.clear()
  } catch {
    // Nothing kept, or the browser refuses to touch it: a later `readKept` checks the account all the same.
  }
}

/** The snapshot of the account of the tab, if one is kept (already read). */
export function keptNow(): Snapshot | null {
  const now = currentAccount()
  return current && (now === null || current.account === now) ? current : null
}

export function keepingFailed(): boolean {
  return failed
}

type State = { snapshot: Snapshot | null; failed: boolean }
let state: State = { snapshot: null, failed: false }

function snapshotState(): State {
  const next = keptNow()
  if (next !== state.snapshot || failed !== state.failed) state = { snapshot: next, failed }
  return state
}

export function useKept(): State {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    snapshotState,
    snapshotState,
  )
}

// Signing out, or another account: what was kept goes before the next one sees anything (EC-1).
onAccountChange(() => {
  if (current && current.account === currentAccount()) return
  void forgetKept()
})

/** For the tests: a shelf of their own, as after a fresh page load. */
export function setShelf(next: Shelf | null): void {
  shelf = next
  current = null
  failed = false
  state = { snapshot: null, failed: false }
  tell()
}
