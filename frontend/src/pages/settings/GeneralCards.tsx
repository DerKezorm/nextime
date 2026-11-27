/**
 * Server › General (plan "Einstellungen", "Allgemein"), the cards in their order: "Grenzen" (the largest attachment and
 * how long the trash keeps things, each with its own save, TIME-1 AC-4, EC-11), "Funktionen für alle Konten" (the
 * switches, AC-5), "Schnittstellen" (the module schluessel's, placed by `SettingsAreas`); "Sprachen" is the module
 * sprachen's (`LanguagesArea`). Built only from the ui kit. The cards "Zustand", the hint
 * without HTTPS, the hint about the proxy and the version line are gone (plan: "Bereit fürs Internet?" under
 * Sign-in and "Über nextime" say it now).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Card, CheckboxRow, Field } from '../../vendor/nexui'
import { Outcome } from './Outcome'
import type { Server } from './ServerCards'

// eslint-disable-next-line react-refresh/only-export-components
export function wholeNumber(text: string, lowest: number, highest: number): { value: number } | { problem: 'empty' | 'range' } {
  const trimmed = text.trim()
  if (!trimmed) return { problem: 'empty' }
  if (!/^\d+$/.test(trimmed)) return { problem: 'range' }
  const value = Number(trimmed)
  if (value < lowest || value > highest) return { problem: 'range' }
  return { value }
}

/** A whole number with its unit and its own save on the height of the field (`Field action`); a wrong value is said at
 * the field and nothing is sent, the old one stays (TIME-1 EC-11). */
function NumberField({ id, label, unit, hint, value, lowest, highest, onSave }: {
  id: string
  label: string
  /** In the label, "Größte Datei je Anhang (MB)" as nexnotes; without one the label stands alone. */
  unit?: string
  hint?: string
  value: number
  lowest: number
  highest: number
  onSave: (value: number) => Promise<boolean>
}) {
  const { t } = useTranslation()
  const [text, setText] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const submit = async () => {
    if (text === null) return
    const read = wholeNumber(text, lowest, highest)
    if ('problem' in read) {
      setProblem(read.problem === 'empty' ? t('server.numberEmpty') : t('server.numberRange', { lowest, highest }))
      return
    }
    setProblem(null)
    if (await onSave(read.value)) setText(null)
  }
  return (
    <form
      data-testid={id}
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <Field
        label={unit ? `${label} (${unit})` : label}
        inputMode="numeric"
        hints={hint ? [hint] : undefined}
        value={text ?? String(value)}
        onChange={(event) => setText(event.target.value)}
        error={problem ?? undefined}
        action={
          <Button kind="secondary" icon="Check" type="submit" disabled={text === null} testId={`${id}-save`}>
            {t('common.save')}
          </Button>
        }
      />
    </form>
  )
}

/** The limits (TIME-1 AC-4, EC-11, EC-12): the largest attachment, how long the trash keeps things; for new things.
 * nextime has no storage per account any more (E24), so the card says nothing of it. */
export function LimitsCard({ server }: { server: Server }) {
  const { t } = useTranslation()
  const s = server.settings
  if (!s) return null
  return (
    <Card id="limits" icon="Gauge" title={t('server.limits')}>
      <NumberField
        id="limit-upload"
        label={t('server.uploadMax')}
        unit={t('server.mb')}
        value={s.upload_max_mb}
        lowest={1}
        highest={10000}
        onSave={(upload_max_mb) => server.save({ upload_max_mb }, t('server.uploadSaved', { count: upload_max_mb }))}
      />
      <NumberField
        id="limit-trash"
        label={t('server.trash')}
        hint={t('server.trashHint')}
        value={s.trash_days}
        lowest={1}
        highest={3650}
        onSave={(trash_days) => server.save({ trash_days }, t('server.trashSaved', { count: trash_days }))}
      />
      <Outcome problem={server.problem} done={server.done} />
    </Card>
  )
}

/** The switches of the features for every account (TIME-1 AC-5): sharing by link, Google (only once it is set up) and
 * address suggestions. "MCP-Zugang" and "Programmschnittstelle (API) für Mitglieder" are the card "Schnittstellen" of
 * the module schluessel, right after this one. Whether nextime looks for new versions is switched under "Über
 * nextime". "Verbindungen ins eigene Netz" has one place since verbinden 0.1.8, the card "Andere nex-Apps" of the
 * module (the same setting, `connections_local_network`, which `PUT /api/settings` still takes, E55). */
export function FeaturesCard({ server }: { server: Server }) {
  const { t } = useTranslation()
  const s = server.settings
  if (!s) return null
  return (
    <Card id="features" icon="SlidersHorizontal" title={t('server.features')} subtitle={t('server.featuresHint')}>
      <div className="flex flex-col gap-3" data-testid="switches">
        <CheckboxRow label={t('server.shareLinks')} hint={t('server.shareLinksHint')} checked={s.share_links} onChange={(share_links) => void server.save({ share_links })} testId="feature-share-links" />
        <CheckboxRow
          label={`${t('server.google')} ${s.google_configured ? t('server.googleSet') : t('server.googleNotSet')}`}
          hint={t('server.googleHint')}
          checked={s.google_enabled}
          disabled={!s.google_configured}
          onChange={(google_enabled) => void server.save({ google_enabled })}
          testId="feature-google"
        />
        <CheckboxRow label={t('server.addresses')} hint={t('server.addressesHint')} checked={s.address_suggestions} onChange={(address_suggestions) => void server.save({ address_suggestions })} testId="feature-addresses" />
      </div>
      <Outcome problem={server.problem} />
    </Card>
  )
}
