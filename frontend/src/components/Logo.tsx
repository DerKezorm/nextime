import { useId } from 'react'

import { useTheme } from '../lib/useTheme'

/** The two looks of the mark "Blatt mit Weiter" (design system "Logo und Name"): the tile with its fine border, the
 * calendar sheet in a gradient, and the arrow forward, cut free by the same path in the tile's color. */
// eslint-disable-next-line react-refresh/only-export-components
export const LOGO_COLORS = {
  light: { tile: '#ffffff', border: '#4a6491', borderOpacity: 0.35, gradient: ['#a9bcdc', '#4a6491', '#26344f'], arrow: '#3d5682', small: '#4a6491' },
  dark: { tile: '#0e1118', border: '#9fb4d8', borderOpacity: 0.45, gradient: ['#dde6f4', '#9fb4d8', '#5a76a6'], arrow: '#c3d2ea', small: '#9fb4d8' },
} as const

const ARROW = 'M40 33l12 9-12 9'

/**
 * The calendar sheet with the arrow forward on its tile, in the look of the app (light or dark). Small (below 24 px)
 * the sheet is drawn in one tone and stronger, without its header line; the rings stay, they make it a calendar.
 */
export function LogoMark({ size = 32, title }: { size?: number; title?: string }) {
  const id = useId().replace(/:/g, '')
  const theme = useTheme()
  const look = LOGO_COLORS[theme]
  const small = size < 24
  const stroke = small ? look.small : `url(#nt-${id})`
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      data-testid="logo-mark"
      data-look={theme}
    >
      {!small && (
        <defs>
          <linearGradient id={`nt-${id}`} gradientUnits="userSpaceOnUse" x1="10" y1="10" x2="54" y2="54">
            <stop offset="0" stopColor={look.gradient[0]} />
            <stop offset=".5" stopColor={look.gradient[1]} />
            <stop offset="1" stopColor={look.gradient[2]} />
          </linearGradient>
        </defs>
      )}
      <rect x="2" y="2" width="60" height="60" rx="16" fill={look.tile} data-part="tile" />
      <rect x="2.75" y="2.75" width="58.5" height="58.5" rx="15.3" fill="none" stroke={look.border} strokeOpacity={look.borderOpacity} strokeWidth="1.5" />
      <rect x="11" y="16" width="32" height="33" rx="6" fill="none" stroke={stroke} strokeWidth={small ? 5 : 4.2} data-part="sheet" />
      {!small && <path d="M11 26h32" stroke={stroke} strokeWidth="3.2" strokeOpacity=".6" data-part="header" />}
      <path d="M20 11.5v8M34 11.5v8" stroke={stroke} strokeWidth={small ? 5 : 4} strokeLinecap="round" data-part="rings" />
      <path d={ARROW} fill="none" stroke={look.tile} strokeWidth={small ? 12 : 11} strokeLinecap="round" strokeLinejoin="round" />
      <path d={ARROW} fill="none" stroke={look.arrow} strokeWidth={small ? 6 : 5} strokeLinecap="round" strokeLinejoin="round" data-part="arrow" />
    </svg>
  )
}

/** The mark with the name, written as the family writes it: NEX in the text's color, TIME in the accent. */
export function Logo({ size = 32, withWordmark = false }: { size?: number; withWordmark?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} title={withWordmark ? undefined : 'nextime'} />
      {withWordmark && <Wordmark />}
    </span>
  )
}

export function Wordmark({ className = 'text-lg' }: { className?: string }) {
  return (
    <span className={className + ' font-extrabold tracking-tight text-mist-100'} data-testid="wordmark" aria-label="nextime">
      <span aria-hidden>NEX</span>
      <span aria-hidden className="nt-wordmark-accent">
        TIME
      </span>
    </span>
  )
}
