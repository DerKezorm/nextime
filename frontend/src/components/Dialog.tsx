import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

/** A dialog over the page: Escape and the scrim close it, focus starts inside and comes back afterwards. */
/** `lead` stands before the title, for a symbol; the title alone names the dialog. */
export function Dialog({ title, onClose, children, wide = false, medium = false, lead }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean; medium?: boolean; lead?: ReactNode }) {
  const { t } = useTranslation()
  const panel = useRef<HTMLDivElement>(null)
  // Callers may pass a new function with every keystroke: it is read when needed, and focus moves only on opening.
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  }, [onClose])
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    const first = panel.current?.querySelector<HTMLElement>('input, textarea, select, button:not([data-close])')
    first?.focus()
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        close.current()
      }
    }
    window.addEventListener('keydown', key, true)
    return () => {
      window.removeEventListener('keydown', key, true)
      before?.focus?.()
    }
  }, [])
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-scrim p-4" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={panel} role="dialog" aria-modal="true" aria-label={title} className={'flex max-h-[calc(100dvh-2rem)] w-full min-w-0 flex-col rounded-[16px] border border-ink-700 bg-ink-850 shadow-2xl shadow-black/50 ' + (wide ? 'max-w-2xl' : medium ? 'max-w-xl' : 'max-w-md')}>
        <div className="flex shrink-0 items-center justify-between border-b border-ink-700/70 px-5 py-3.5">
          <h2 className="flex min-w-0 items-center gap-2 text-base font-semibold text-mist-100">
            {lead}
            <span className="min-w-0">{title}</span>
          </h2>
          <button type="button" data-close onClick={onClose} aria-label={t('common.close')} className="rounded-full p-1.5 text-mist-500 hover:bg-ink-800 hover:text-mist-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        {/* Taller than the screen (the connect assistant of a firm with many accounts, a long form on a phone): the
            inside scrolls, the title stays. */}
        <div className="nt-scroll min-h-0 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  )
}
