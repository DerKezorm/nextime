/**
 * The uploads of this tab (TIME-9), as nexnotes keeps them (`attachments/uploads.ts` there): at most `AT_ONCE` at the
 * same time, the rest wait in turn, each with its own progress (AC-6, AC-7) and "Abbrechen"; a file larger than the
 * limit is refused before it is sent, with "Ausblenden" (AC-2, EC-5); a broken connection shows "Erneut hochladen"
 * (EC-11). The store lives outside React, so an upload goes on when the side panel closes after "Sichern" and lands at
 * the saved event (AC-10); closing without saving cancels what belongs to the panel.
 */
import { useSyncExternalStore } from 'react'

import { ApiError } from '../../api/client'
import { onAccountChange } from '../../state/account'
import { newAttachmentId, sendFile, type AttachmentInfo, type Sending, type Target } from './api'

export const AT_ONCE = 3
const MB = 1024 * 1024

export type UploadState = 'waiting' | 'uploading' | 'failed' | 'too_large'

export type Upload = {
  id: string
  key: string
  name: string
  size: number
  state: UploadState
  /** 0 to 1 while uploading. */
  progress: number
  /** Why it failed: `network`, `proxy_too_large` or the server's code. */
  error?: string
  values?: Record<string, unknown>
  max?: number
}

type Entry = Upload & { file: Blob; target: Target; sending?: Sending }

const entries = new Map<string, Entry>()
const listeners = new Set<() => void>()
const finished = new Set<(key: string, info: AttachmentInfo) => void>()
let snapshot: Upload[] = []

export function keyOf(target: Target): string {
  return 'event' in target ? `e:${target.event}` : `r:${target.ref}`
}

function publish(): void {
  snapshot = [...entries.values()].map(({ file: _file, sending: _sending, target: _target, ...shown }) => ({ ...shown }))
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Heard when an upload is through: the panel shows the attachment. */
export function onUploaded(listener: (key: string, info: AttachmentInfo) => void): () => void {
  finished.add(listener)
  return () => finished.delete(listener)
}

function running(): number {
  return [...entries.values()].filter((entry) => entry.state === 'uploading').length
}

function pump(): void {
  for (const entry of entries.values()) {
    if (running() >= AT_ONCE) return
    if (entry.state === 'waiting') start(entry)
  }
}

function start(entry: Entry): void {
  entry.state = 'uploading'
  entry.progress = 0
  entry.error = undefined
  const sending = sendFile(entry.target, entry.id, entry.file, entry.name, (part) => {
    if (entries.get(entry.id) !== entry) return
    entry.progress = part
    publish()
  })
  entry.sending = sending
  publish()
  sending.done.then(
    (info) => {
      if (entries.get(entry.id) === entry) entries.delete(entry.id)
      publish()
      for (const listener of finished) listener(entry.key, info)
      pump()
    },
    (error: unknown) => {
      if (entries.get(entry.id) !== entry) return pump()
      const code = error instanceof ApiError ? error.code : 'network'
      if (code === 'cancelled') entries.delete(entry.id)
      else {
        entry.state = code === 'attachment_too_large' ? 'too_large' : 'failed'
        entry.error = code
        entry.values = error instanceof ApiError ? error.values : undefined
        entry.sending = undefined
      }
      publish()
      pump()
    },
  )
}

/** Files for an event (or one being made): each its own upload; too large ones are refused at once. */
export function enqueue(target: Target, files: File[], maxMb: number): void {
  const key = keyOf(target)
  for (const file of files) {
    const id = newAttachmentId()
    const tooLarge = file.size > maxMb * MB
    entries.set(id, { id, key, name: file.name, size: file.size, state: tooLarge ? 'too_large' : 'waiting', progress: 0, file, target, max: maxMb })
  }
  publish()
  pump()
}

/** "Abbrechen" (AC-6) and "Ausblenden": the upload stops, nothing of it stays at the event. */
export function cancelUpload(id: string): void {
  const entry = entries.get(id)
  if (!entry) return
  entries.delete(id)
  entry.sending?.cancel()
  publish()
  pump()
}

/** Every upload of an event or of one being made: the panel closed without saving. */
export function cancelAll(target: Target): void {
  const key = keyOf(target)
  for (const entry of [...entries.values()]) if (entry.key === key) cancelUpload(entry.id)
}

/** "Erneut hochladen" after the connection broke (EC-11). */
export function retryUpload(id: string): void {
  const entry = entries.get(id)
  if (!entry || entry.state !== 'failed') return
  entry.state = 'waiting'
  entry.error = undefined
  publish()
  pump()
}

export function useUploads(target: Target | null): Upload[] {
  const all = useSyncExternalStore(subscribe, () => snapshot)
  if (!target) return []
  const key = keyOf(target)
  return all.filter((each) => each.key === key)
}

/** Uploads still running or waiting for an event (or one being made). */
export function busyFor(target: Target): boolean {
  const key = keyOf(target)
  return [...entries.values()].some((entry) => entry.key === key && (entry.state === 'uploading' || entry.state === 'waiting'))
}

/** For tests: every upload of the tab, and a way to forget them. */
export function uploadsNow(): Upload[] {
  return snapshot
}

export function forgetUploads(): void {
  for (const entry of entries.values()) entry.sending?.cancel()
  entries.clear()
  publish()
}

onAccountChange(forgetUploads)
