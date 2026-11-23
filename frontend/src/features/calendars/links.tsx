/**
 * Notes with links (TIME-5 AC-4, EC-7): everything stays text; only web, mail and phone addresses become links, and
 * the first link to a video meeting (Zoom, Google Meet, Microsoft Teams, Webex, Jitsi, FaceTime) gets "Join".
 */
import type { ReactNode } from 'react'

const LINK = /(https?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)\]]|mailto:[^\s<>"']+[^\s<>"'.,;:!?)\]]|tel:\+?[\d][\d\s/-]*\d)/gi

/** The parts of a text: plain text and links, in order. */
// eslint-disable-next-line react-refresh/only-export-components
export function linkParts(text: string): { text: string; href?: string }[] {
  const parts: { text: string; href?: string }[] = []
  let last = 0
  for (const found of text.matchAll(LINK)) {
    const start = found.index ?? 0
    if (start > last) parts.push({ text: text.slice(last, start) })
    const href = found[0].toLowerCase().startsWith('tel:') ? found[0].replace(/[\s/-]/g, '') : found[0]
    parts.push({ text: found[0], href })
    last = start + found[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last) })
  return parts
}

const MEETINGS: ((host: string) => boolean)[] = [
  (host) => host === 'zoom.us' || host.endsWith('.zoom.us'),
  (host) => host === 'meet.google.com',
  (host) => host === 'teams.microsoft.com' || host === 'teams.live.com',
  (host) => host === 'webex.com' || host.endsWith('.webex.com'),
  (host) => host === 'meet.jit.si' || host.startsWith('jitsi.') || host.includes('.jitsi.'),
  (host) => host === 'facetime.apple.com',
]

/** The first link to a video meeting in the notes, or null. */
// eslint-disable-next-line react-refresh/only-export-components
export function joinLink(text: string): string | null {
  for (const part of linkParts(text)) {
    if (!part.href || !/^https:\/\//i.test(part.href)) continue
    try {
      const host = new URL(part.href).hostname.toLowerCase()
      if (MEETINGS.some((meets) => meets(host))) return part.href
    } catch {
      continue
    }
  }
  return null
}

export function LinkedText({ text }: { text: string }): ReactNode {
  return linkParts(text).map((part, index) =>
    part.href ? (
      <a key={index} href={part.href} target="_blank" rel="noopener noreferrer" className="text-accent-400 underline-offset-2 hover:underline">
        {part.text}
      </a>
    ) : (
      <span key={index}>{part.text}</span>
    ),
  )
}
