/**
 * The operator's settings of the server as the cards of Server › General read and save them (`/api/settings`, TIME-1):
 * the switch moves at once, the server's answer confirms it, a refusal puts it back. The cards themselves are in
 * `GeneralCards.tsx` (ui kit); Sign-in, Mail and Storage and backups come from the shared modules.
 */
import { useCallback, useEffect, useState } from 'react'

import { ApiError, settingsApi, type ServerSettings, type SettingsChange } from '../../api/client'
import type { AddressQuestion } from '../../vendor/nexkonten/SignInPage'
import { ASKED, useAction } from './actions'

export type Server = ReturnType<typeof useServerSettings>

export function useServerSettings() {
  const [settings, setSettings] = useState<ServerSettings | null>(null)
  const action = useAction()
  useEffect(() => {
    settingsApi.read().then(setSettings, () => undefined)
  }, [])
  /** The switch moves at once; the server's answer confirms it, a refusal puts it back. */
  const save = async (change: SettingsChange, done?: string) => {
    const before = settings
    if (settings) setSettings({ ...settings, ...change } as ServerSettings)
    const ok = await action.run(async () => setSettings(await settingsApi.save(change)), done)
    if (!ok) setSettings(before)
    return ok
  }
  /**
   * Saves the public address. authentik, set up by the button, knows the old one: the server then answers 409
   * `public_url_change` and saves nothing. That comes back as the question to put to the operator.
   */
  const saveAddress = async (public_url: string, confirm: boolean, done?: string): Promise<AddressQuestion | null> => {
    let question: AddressQuestion | null = null
    await action.run(async () => {
      try {
        setSettings(await settingsApi.save({ public_url, confirm_address: confirm }))
      } catch (error) {
        if (!(error instanceof ApiError) || error.code !== 'public_url_change') throw error
        question = { count: Number(error.values.count ?? 0), hand: Number(error.values.hand ?? 0), address: String(error.values.address ?? '') }
        return ASKED
      }
    }, done)
    return question
  }
  const reload = useCallback(() => settingsApi.read().then(setSettings, () => undefined), [])
  return { settings, save, saveAddress, reload, ...action }
}

