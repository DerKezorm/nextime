/**
 * A short notice at the bottom of the page, as in nexnotes (`folders/notice.ts` there): one at a time, a new one takes
 * the place of the old, gone after five seconds; with "Rückgängig" it stays ten. A success says so here instead of a
 * box in the page ("„Kino“ ist wieder da."), a deletion with the way back where the server has one.
 */
import { useSyncExternalStore } from 'react'

import { onAccountChange } from '../state/account'

/** How long "Undo" stays (as in nexnotes: 10 seconds). */
export const UNDO_MS = 10_000
/** How long a notice without "Undo" stays. */
export const NOTICE_MS = 5000

/** `undo` gives back what to say afterwards, or nothing to just close the notice. */
export type Notice = { text: string; undo?: () => Promise<string | void> }

let notice: (Notice & { key: number }) | null = null
let counter = 0
let timer: ReturnType<typeof setTimeout> | null = null
const listeners = new Set<() => void>()

function publish(next: typeof notice): void {
  notice = next
  for (const listener of [...listeners]) listener()
}

export function hideNotice(): void {
  if (timer) clearTimeout(timer)
  timer = null
  publish(null)
}

/** Shows a notice; with `undo`, the button "Undo" until the time is up. A new notice takes the place of the old. */
export function showNotice(next: Notice, ms = next.undo ? UNDO_MS : NOTICE_MS): void {
  if (timer) clearTimeout(timer)
  counter += 1
  publish({ ...next, key: counter })
  timer = setTimeout(hideNotice, ms)
}

export function useNotice() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => notice,
  )
}

// A notice names something of the account before and could undo it: it goes.
onAccountChange(hideNotice)
