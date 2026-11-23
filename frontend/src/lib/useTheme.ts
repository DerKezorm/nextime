/** The look the page shows now (light or dark), following every switch: `data-theme` on the root (`theme.ts`). */
import { useSyncExternalStore } from 'react'

import type { Theme } from './theme'

function current(): Theme {
  return typeof document !== 'undefined' && document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'
}

function subscribe(listener: () => void): () => void {
  if (typeof MutationObserver === 'undefined') return () => undefined
  const observer = new MutationObserver(listener)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => observer.disconnect()
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, current, current)
}
