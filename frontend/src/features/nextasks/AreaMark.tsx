/**
 * The mark of an area or a list of nextasks: its symbol in its colour as nextasks stores them, drawn like a calendar's
 * symbol (a line drawing, no filled tile); without a symbol in nextasks, the symbol of tasks.
 */
import { createElement } from 'react'

import { colorOfTask, symbolOf } from './look'

export function AreaMark({ area, dim = false, size = 14 }: { area: { icon?: string; color?: string }; dim?: boolean; size?: number }) {
  const color = colorOfTask(area.color)
  return createElement(symbolOf(area.icon), {
    'aria-hidden': true,
    className: 'shrink-0 ' + (color ? '' : dim ? 'text-mist-600' : 'text-mist-400'),
    style: { width: size + 2, height: size + 2, color: color ?? undefined, opacity: dim && color ? 0.45 : 1 },
    strokeWidth: 2.2,
    'data-testid': 'area-mark',
    'data-symbol': area.icon || undefined,
    'data-color': area.color || undefined,
  } as Parameters<typeof createElement>[1])
}
