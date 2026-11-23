/**
 * Attachments on the server (`backend/app/routers/attachments.py`, TIME-9), as nexnotes uploads them: the file as the
 * body of one request, with progress and a way to cancel (AC-6), at an event or, for an event being made, at the
 * `client_ref` it will be made with (AC-10).
 */
import { api, ApiError, tabId } from '../../api/client'

export type AttachmentKind = 'image' | 'pdf' | 'file'

/** An attachment as the server describes it. */
export type AttachmentInfo = {
  id: string
  name: string
  kind: AttachmentKind
  mime: string
  size: number
  pages: number | null
  width: number | null
  height: number | null
  /** A WebP every browser shows exists (`/view`), a HEIC photo too. */
  view: boolean
  /** A preview exists (`/thumb`). */
  thumb: boolean
  /** The file itself may be shown in the page (a picture drawn anew, a PDF); otherwise only downloaded. */
  inline: boolean
  created_at: string | null
}

/** An attachment of an event from iCloud, Google or a CalDAV server: shown, never added to (AC-5). */
export type RemoteAttachment = { index: number; name: string; mime: string; size: number | null; link: string }

export type Target = { event: number } | { ref: string }

const base = (id: string) => `/api/attachments/${encodeURIComponent(id)}`

export const fileUrl = (id: string, download = false) => `${base(id)}/file${download ? '?download=1' : ''}`
export const viewUrl = (id: string) => `${base(id)}/view`
export const thumbUrl = (id: string) => `${base(id)}/thumb`
export const remoteUrl = (eventId: number, index: number) => `/api/events/${eventId}/remote-attachments/${index}`

export const attachmentsApi = {
  remove: (id: string) => api<void>(base(id), { method: 'DELETE' }),
}

/** A new attachment id: 16 random bytes as base64url, the shape the server takes. */
export function newAttachmentId(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  let text = ''
  for (const byte of bytes) text += String.fromCharCode(byte)
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export type Sending = { done: Promise<AttachmentInfo>; cancel: () => void }

/** Uploads one file: progress from 0 to 1 while it goes, `cancel` stops it. A broken connection rejects with the code
 * `network`, a refusal with the server's code and values; a proxy that refuses a large body without nextime's answer
 * comes as `proxy_too_large` (EC-12). */
export function sendFile(target: Target, id: string, file: Blob, name: string, onProgress: (part: number) => void): Sending {
  const request = new XMLHttpRequest()
  const done = new Promise<AttachmentInfo>((resolve, reject) => {
    const query = new URLSearchParams({ id, name })
    if ('event' in target) query.set('event_id', String(target.event))
    else query.set('ref', target.ref)
    request.open('POST', `/api/attachments?${query.toString()}`)
    request.setRequestHeader('X-Nextime-Client', tabId())
    request.setRequestHeader('Accept', 'application/json')
    request.setRequestHeader('Content-Type', 'application/octet-stream')
    request.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total)
    }
    request.onload = () => {
      let data: { detail?: Record<string, unknown> } | AttachmentInfo | null
      try {
        data = JSON.parse(request.responseText)
      } catch {
        data = null
      }
      if (request.status >= 200 && request.status < 300) return resolve(data as AttachmentInfo)
      const detail = (data as { detail?: Record<string, unknown> } | null)?.detail
      if (!detail && request.status === 413) return reject(new ApiError(413, 'proxy_too_large'))
      const { code, message: _message, ...values } = (detail ?? {}) as { code?: string; message?: string }
      reject(new ApiError(request.status, typeof code === 'string' ? code : 'internal_error', values))
    }
    request.onerror = () => reject(new ApiError(0, 'network'))
    request.ontimeout = () => reject(new ApiError(0, 'network'))
    request.onabort = () => reject(new ApiError(0, 'cancelled'))
    request.send(file)
  })
  return { done, cancel: () => request.abort() }
}
