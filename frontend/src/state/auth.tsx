/**
 * Who is signed in. `loading` until the server answered, `setup` while nextime has no account yet, `signedOut`,
 * or `signedIn` with the account; `offline` while the server does not answer at all (no network, or a proxy says it is
 * down): the installed app opened without network shows "No connection" instead of the sign-in, and asks again every
 * few seconds and as soon as the network is back.
 *
 * A session that ends while the page is open does not throw the page away: what is being written stays on screen.
 * `interrupted` says why: `expired` (the session ran out; a dialog asks to sign in again, and afterwards
 * `SIGNED_IN_AGAIN_EVENT` lets every open form save what waited, TIME-2 EC-4) or `blocked` (the operator blocked or
 * deleted the account: "Your account is blocked", and nothing more is saved, TIME-2 EC-5).
 *
 * Signing out and every change of the account in the tab forget what the stores kept of the account before
 * (`state/account.ts`), before the next account's page is drawn. The open page asks now and then who is signed in; if
 * another tab signed in as someone else meanwhile, this page follows at once; changes typed here for the account
 * before that still waited are lost then.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { ApiError, authApi, SIGNED_OUT_EVENT, type Me } from '../api/client'
import i18n, { changeLanguage } from '../i18n'
import { signedOut } from '../lib/devicePref'
import { markUnreachable } from '../features/offline/connection'
import { forgetKept, readKept } from '../features/offline/snapshot'
import { followAccount } from './account'

type Status = 'loading' | 'setup' | 'signedOut' | 'signedIn' | 'offline'
export type Interruption = 'expired' | 'blocked' | null

/** Sent on `window` once the account signed in again after its session ran out: whatever waited is saved now. */
export const SIGNED_IN_AGAIN_EVENT = 'nextime:signed-in-again'

type Auth = {
  status: Status
  me: Me | null
  /** The session ended while the page was open, and why. */
  interrupted: Interruption
  /** The page was sent to the sign-in because its session ended (not by signing out here). */
  ended: boolean
  refresh: () => Promise<void>
  setMe: (me: Me) => void
  signOut: () => Promise<void>
  /** Signed in again in the dialog: the page goes on where it was. */
  resume: (me: Me) => void
}

const Context = createContext<Auth | null>(null)

/** How often an open page asks whether its session still holds: a block shows within seconds. */
export const SESSION_MS = 5_000
/** How often a page without the server asks again. */
export const OFFLINE_RETRY_MS = 5_000

/** Signing out without a connection (TIME-28 EC-1): the sign-out waits here and goes to the server first at the next
 * start with a connection, before anything else; until then the app counts as signed out. */
export const PENDING_SIGN_OUT = 'nextime.signOutPending'

function signOutPending(): boolean {
  try {
    return localStorage.getItem(PENDING_SIGN_OUT) === '1'
  } catch {
    return false
  }
}

function setSignOutPending(on: boolean): void {
  try {
    if (on) localStorage.setItem(PENDING_SIGN_OUT, '1')
    else localStorage.removeItem(PENDING_SIGN_OUT)
  } catch {
    // A browser without storage: the sign-out is tried again by hand.
  }
}

/** No answer of the server at all: the network is gone (fetch fails), or a proxy says the server is down. */
// eslint-disable-next-line react-refresh/only-export-components
export function unreachable(error: unknown): boolean {
  if (error instanceof ApiError) return error.status === 502 || error.status === 503 || error.status === 504
  return error instanceof TypeError
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [me, setMeState] = useState<Me | null>(null)
  const [interrupted, setInterrupted] = useState<Interruption>(null)
  const [ended, setEnded] = useState(false)

  const setMe = useCallback((next: Me) => {
    // Before anything of this account is drawn: the stores forget the one before.
    followAccount(next.id)
    setMeState(next)
    setInterrupted(null)
    setEnded(false)
    setStatus('signedIn')
    // The account's language wins over the browser's, on every device (TIME-3 AC-1).
    if (next.language && next.language !== i18n.language) void changeLanguage(next.language, false)
  }, [])

  const refresh = useCallback(async () => {
    if (signOutPending()) {
      // A sign-out made without a connection goes first: nobody is signed in again by the cookie it left (EC-1).
      try {
        await authApi.logout()
        setSignOutPending(false)
      } catch (error) {
        if (unreachable(error)) {
          void forgetKept()
          followAccount(null)
          setMeState(null)
          setStatus('signedOut')
          return
        }
        // The server answered (the session was gone already): nothing waits any more.
        setSignOutPending(false)
      }
    }
    try {
      const state = await authApi.setupState()
      if (state.needs_setup) {
        setStatus('setup')
        return
      }
      if (!state.signed_in) {
        // Nobody signed in on this device: what the installed app kept for reading offline goes (TIME-28 EC-1).
        void forgetKept()
        followAccount(null)
        setStatus('signedOut')
        setMeState(null)
        return
      }
      setMe(await authApi.me())
    } catch (error) {
      if (unreachable(error)) {
        // The installed app kept the next weeks of the account signed in last: it shows them, to read only, until the
        // server answers again (TIME-28 AC-3). A session that ran out meanwhile asks for signing in then (EC-6).
        const kept = await readKept()
        if (kept) {
          markUnreachable()
          setMe(kept.me)
          return
        }
        setStatus('offline')
        return
      }
      setStatus('signedOut')
      if (error instanceof ApiError && error.status === 401) {
        followAccount(null)
        setMeState(null)
      }
    }
  }, [setMe])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Without the server: ask again every few seconds, and at once when the network is back.
  useEffect(() => {
    if (status !== 'offline') return
    const again = () => void refresh()
    const timer = window.setInterval(again, OFFLINE_RETRY_MS)
    window.addEventListener('online', again)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('online', again)
    }
  }, [status, refresh])

  useEffect(() => {
    const gone = (event: Event) => {
      const code = (event as CustomEvent<{ code?: string }>).detail?.code
      // Signed in a moment ago: keep the page and say why; otherwise there is nothing on screen to keep.
      setStatus((now) => {
        if (now === 'signedIn') {
          setInterrupted(code === 'account_blocked' ? 'blocked' : 'expired')
          return now
        }
        setEnded(true)
        return 'signedOut'
      })
    }
    window.addEventListener(SIGNED_OUT_EVENT, gone)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, gone)
  }, [])

  const meId = useRef<number | null>(null)
  meId.current = me?.id ?? null

  useEffect(() => {
    if (status !== 'signedIn' || interrupted) return
    // A 401 sends SIGNED_OUT_EVENT (api/client.ts); anything else, such as the server restarting, changes nothing.
    // Another account (signed in in another tab, the cookie is shared): the page follows it.
    const ask = () => {
      if (document.visibilityState !== 'visible') return
      void authApi
        .me()
        .then((now) => {
          if (now && typeof now.id === 'number' && meId.current !== null && now.id !== meId.current) setMe(now)
        })
        .catch(() => undefined)
    }
    const session = window.setInterval(ask, SESSION_MS)
    document.addEventListener('visibilitychange', ask)
    return () => {
      window.clearInterval(session)
      document.removeEventListener('visibilitychange', ask)
    }
  }, [status, interrupted, setMe])

  const signOut = useCallback(async () => {
    try {
      await authApi.logout()
    } catch (error) {
      // Without a connection the session stays on the server: the sign-out waits and goes first next time (EC-1).
      if (unreachable(error)) setSignOutPending(true)
    }
    void forgetKept()
    if (meId.current !== null) signedOut(meId.current)
    followAccount(null)
    setEnded(false)
    setInterrupted(null)
    setMeState(null)
    setStatus('signedOut')
  }, [])

  const resume = useCallback(
    (next: Me) => {
      setMe(next)
      window.dispatchEvent(new Event(SIGNED_IN_AGAIN_EVENT))
    },
    [setMe],
  )

  const value = useMemo(
    () => ({ status, me, interrupted, ended, refresh, setMe, signOut, resume }),
    [status, me, interrupted, ended, refresh, setMe, signOut, resume],
  )
  return <Context.Provider value={value}>{children}</Context.Provider>
}

/** The account signed in, or null; also outside the provider (a part drawn on its own, as in a test). */
// eslint-disable-next-line react-refresh/only-export-components
export function useAccountId(): number | null {
  return useContext(Context)?.me?.id ?? null
}

/** The account signed in, or null; also outside the provider. */
// eslint-disable-next-line react-refresh/only-export-components
export function useMe(): Me | null {
  return useContext(Context)?.me ?? null
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): Auth {
  const value = useContext(Context)
  if (!value) throw new Error('useAuth outside AuthProvider')
  return value
}

/** Only addresses inside nextime, so a link cannot send someone elsewhere after signing in. */
// eslint-disable-next-line react-refresh/only-export-components
export function safeNext(next: string | null): string {
  return next && next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\') ? next : '/'
}
