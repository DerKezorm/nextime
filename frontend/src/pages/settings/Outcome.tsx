/**
 * What a change in a card of the settings came to, in the kit's boxes: what went wrong (`Notice danger`, the sentence
 * of the server's code with its values; for an account from a provider that must confirm, the buttons to sign in
 * there once more), or that it worked (`Notice ok`). Nothing when there is nothing to say.
 */
import { useTranslation } from 'react-i18next'

import { ApiError } from '../../api/client'
import { errorText, problemText } from '../../lib/errors'
import { go, startAddress } from '../../lib/providers'
import { Button, Buttons, Notice } from '../../vendor/nexui'
import type { Trouble } from './actions'

export function Outcome({ problem, done = null, testId }: { problem: Trouble; done?: string | null; testId?: string }) {
  if (!problem && !done) return null
  return (
    <div aria-live="polite" data-testid={testId}>
      {problem ? <Notice tone="danger">{typeof problem === 'string' ? errorText(problem) : problemText(problem)}</Notice> : <Notice tone="ok">{done}</Notice>}
      {problem instanceof ApiError && problem.code === 'fresh_sign_in_required' && <SignInAgain providers={problem.values.providers} />}
    </div>
  )
}

/** An account that signs in through a provider confirms an operator act with a fresh sign-in there: a button per
 * provider of the account, back to this page afterwards (the server asks the provider for `prompt=login`). */
export function SignInAgain({ providers }: { providers: unknown }) {
  const { t } = useTranslation()
  const list = Array.isArray(providers) ? (providers as { slug: string; label: string }[]) : []
  const here = window.location.pathname + window.location.search
  return (
    <div className="mt-2" data-testid="sign-in-again">
      <Buttons>
        {list.map((provider) => (
          <Button key={provider.slug} kind="secondary" size="sm" icon="LogIn" onClick={() => go(startAddress(provider.slug, { next: here, fresh: true }))}>
            {t('oidc.login.button', { name: provider.label })}
          </Button>
        ))}
      </Buttons>
    </div>
  )
}
