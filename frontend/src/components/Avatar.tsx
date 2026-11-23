/**
 * A person's profile picture (TIME-2 AC-5), or the first letter of the name on a circle of the person's own color where
 * there is none or the server does not show it to this viewer (EC-11). The address carries the time the picture was
 * set, so a new one is a new address.
 */
import { useState } from 'react'

import { avatarUrl, initialOf, personColor } from '../lib/avatar'

export function Avatar({
  accountId,
  name,
  stamp,
  size = 28,
  className = 'text-white',
  ring,
  color,
}: {
  accountId: number
  name: string
  stamp?: string | null
  size?: number
  /** The look of the letter's circle. */
  className?: string
  /** A border in a color of its own. */
  ring?: string
  /** The color the server gave the person (``Me.color``); without one, a color from the id. */
  color?: number | null
}) {
  const url = avatarUrl(accountId, stamp)
  const [failed, setFailed] = useState<string | null>(null)
  const style = { width: size, height: size, ...(ring ? { borderColor: ring, borderWidth: 2, borderStyle: 'solid' } : {}) }
  if (url && failed !== url)
    return (
      <img
        src={url}
        alt=""
        aria-hidden
        width={size}
        height={size}
        onError={() => setFailed(url)}
        className="shrink-0 rounded-full object-cover"
        style={style}
        data-testid="avatar-picture"
      />
    )
  return (
    <span
      aria-hidden
      className={'flex shrink-0 items-center justify-center rounded-full text-xs font-bold ' + className}
      style={{ ...style, background: personColor(color ?? accountId), fontSize: Math.max(10, Math.round(size * 0.42)) }}
      data-testid="avatar-initial"
    >
      {initialOf(name)}
    </span>
  )
}
