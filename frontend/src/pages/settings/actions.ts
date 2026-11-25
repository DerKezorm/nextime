/**
 * What the cards of the settings share besides their looks (those come from the ui kit): running a change and
 * remembering whether it worked, and handing a file to the browser.
 */
import { useCallback, useState } from 'react'

import { ApiError } from '../../api/client'

/** What went wrong: a code, or the server's refusal with the values its sentence needs ("({{max_mb}} MB)"). */
export type Trouble = string | ApiError | null

/** What a `run` job returns when it has asked a question instead of working: no success message, no problem. */
export const ASKED = Symbol('asked')

/** Runs a change, remembers whether it worked; the card shows it with `Outcome` (or `Feedback`). */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Trouble>(null)
  const [done, setDone] = useState<string | null>(null)
  const run = useCallback(async (work: () => Promise<unknown>, success?: string): Promise<boolean> => {
    setBusy(true)
    setProblem(null)
    setDone(null)
    try {
      const result = await work()
      if (result === ASKED) return false
      if (success) setDone(success)
      return true
    } catch (error) {
      // The refusal itself, so its sentence gets the server's values, as on the account page.
      setProblem(error instanceof ApiError ? error : 'internal_error')
      return false
    } finally {
      setBusy(false)
    }
  }, [])
  return { busy, problem, done, run }
}

export function saveAsFile(name: string, data: Blob) {
  const url = URL.createObjectURL(data)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
