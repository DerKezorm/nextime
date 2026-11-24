/**
 * A small stand-in for the server and a page to draw into, for the tests of the pages: `serve` answers each request,
 * `calls` keeps what was asked, `render` draws at an address, `settle` lets the answers arrive.
 */
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'

import type { Me } from './api/client'
import { forgetCalendars } from './features/calendars/store'
import { forgetShownWindows } from './features/views/state'

export type Answer = { status: number; body: unknown }
type Handler = (method: string, path: string, body: unknown) => Answer | undefined

let handler: Handler = () => undefined
export const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = []
let root: Root | null = null
export let box: HTMLDivElement

export function serve(next: Handler): void {
  handler = next
}

export function ok(body: unknown): Answer {
  return { status: 200, body }
}

export function me(change: Partial<Me> = {}): Me {
  return {
    id: 1,
    name: 'anna',
    display_name: 'Anna',
    email: 'anna@example.com',
    role: 'member',
    sign_in: 'password',
    language: '',
    two_factor: false,
    two_factor_recovery_left: 0,
    version: '0.1.0',
    whats_new_seen: '0.1.0',
    min_password: 12,
    display: { week_start: 1, week_numbers: true, clock: 'auto', time_zone: '', home_zone: '' },
    ...change,
  }
}

/** The answers of a server that is set up, with `account` signed in. */
export function signedIn(account: Me, more: Handler = () => undefined): Handler {
  return (method, path, body) =>
    more(method, path, body) ??
    (path === '/api/setup'
      ? ok({ needs_setup: false, code_required: true, signed_in: true, version: '0.1.0', min_password: 12 })
      : path === '/api/auth/me' || path === '/api/me/whats-new/seen'
        ? ok(account)
        : path === '/api/auth/methods'
          ? ok({ password: true, providers: [] })
          : path === '/api/oidc/me'
            ? ok([])
            : path.startsWith('/api/locales')
              ? ok([])
              : undefined)
}

export function start(): void {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  calls.length = 0
  handler = () => undefined
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
      calls.push({ method, path, body, headers: (init?.headers ?? {}) as Record<string, string> })
      const answer = handler(method, path, body) ?? { status: 200, body: [] }
      return new Response(JSON.stringify(answer.body), { status: answer.status, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  box = document.createElement('div')
  document.body.append(box)
  root = createRoot(box)
}

export function stop(): void {
  act(() => root?.unmount())
  root = null
  box.remove()
  forgetShownWindows()
  forgetCalendars()
  vi.unstubAllGlobals()
}

export async function settle(): Promise<void> {
  for (let i = 0; i < 15; i++) await act(async () => await Promise.resolve())
}

/** Draws `node` at the address `at`, as a page opened anew (whatever was drawn before goes). */
export async function render(node: ReactNode, at = '/'): Promise<void> {
  act(() => root?.unmount())
  root = createRoot(box)
  await act(async () => root!.render(<MemoryRouter initialEntries={[at]}>{node}</MemoryRouter>))
  await settle()
}

export function type(field: HTMLInputElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, value)
  act(() => void field.dispatchEvent(new Event('input', { bubbles: true })))
}

export async function choose(select: HTMLSelectElement, value: string): Promise<void> {
  Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, value)
  await act(async () => void select.dispatchEvent(new Event('change', { bubbles: true })))
  await settle()
}

export async function click(element: Element | null | undefined): Promise<void> {
  if (!element) throw new Error('nothing to click')
  await act(async () => (element as HTMLElement).click())
  await settle()
}

/** The button or link whose text is `text`. */
export function byText(text: string, scope: ParentNode = document): HTMLElement | undefined {
  return [...scope.querySelectorAll<HTMLElement>('button, a, [role="menuitem"]')].find((element) => element.textContent?.trim() === text)
}
