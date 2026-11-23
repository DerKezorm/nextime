/**
 * A small calendar sheet with the two rings of the nextime mark: the weekday on a tinted head, the day below. The
 * column beside Month and Week and "Nächste Tage" in Today use it alike (decided while testing, 2026-11-23).
 */
import { format } from './dates'

/** `color`: a CSS color, the calendar's (`colorVar(...)`), the accent or a quiet gray. */
export function DateSheet({ day, language, color, testId = 'column-date' }: { day: string; language: string; color: string; testId?: string }) {
  return (
    <span
      className="relative mt-1 flex h-[42px] w-10 shrink-0 flex-col overflow-visible rounded-[8px] border bg-ink-850 leading-none"
      style={{ borderColor: `color-mix(in srgb, ${color} 40%, transparent)` }}
      aria-hidden
      data-testid={testId}
    >
      <span className="absolute -top-[3px] left-[11px] h-[6px] w-[2.5px] rounded-full" style={{ background: color }} />
      <span className="absolute -top-[3px] right-[11px] h-[6px] w-[2.5px] rounded-full" style={{ background: color }} />
      <span className="rounded-t-[7px] pt-[5px] pb-[2px] text-center text-[9px] font-semibold tracking-wide uppercase" style={{ background: `color-mix(in srgb, ${color} 14%, transparent)`, color }}>
        {format(day, language, { weekday: 'short' }).replace('.', '')}
      </span>
      <span className="flex flex-1 items-center justify-center text-[16px] font-semibold text-mist-100">{Number(day.slice(8, 10))}</span>
    </span>
  )
}
