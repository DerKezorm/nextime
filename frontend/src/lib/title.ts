import { useEffect } from 'react'

/** The browser tab says where one is: "Ideen · nextime" instead of "nextime" on every page. */
export function pageTitle(text: string | null | undefined): string {
  const name = (text ?? '').trim()
  return name ? `${name} · nextime` : 'nextime'
}

export function useTitle(text: string | null | undefined): void {
  useEffect(() => {
    document.title = pageTitle(text)
  }, [text])
}
