/**
 * "Anhänge" in the side panel of an event (TIME-9, T9-Anhaenge), laid out like nexnotes' attachment blocks: each file
 * with its preview (a picture, a PDF's first page with its number of pages; other files an icon), name and size, opened
 * or downloaded with a click, × with a question for whoever may change the event (AC-1, AC-9); uploads with name,
 * progress in percent and "Abbrechen" (AC-6), a refused file with its size and the limit and "Ausblenden" (AC-2), a
 * broken one with "Erneut hochladen" (EC-11); the surface "Dateien hierher ziehen oder auswählen" (AC-7, EC-4, EC-17)
 * and the sentence about guests (AC-8). Names are text, cut short in the list and whole in their title (EC-6, EC-8).
 *
 * At an event of iCloud, Google or a CalDAV server: "Anhänge gibt es nur an Terminen in nextime-Kalendern." and the
 * files the source has, fetched through nextime; one that cannot be had says so (AC-5, EC-14).
 */
import { File as FileIcon, FileText, X } from 'lucide-react'
import { useEffect, useId, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { Dialog } from '../../components/Dialog'
import { errorText, problemText } from '../../lib/errors'
import { sizeText } from './size'
import { attachmentsApi, fileUrl, remoteUrl, thumbUrl, type AttachmentInfo, type RemoteAttachment, type Target } from './api'
import { cancelUpload, enqueue, keyOf, onUploaded, retryUpload, useUploads, type Upload } from './uploads'

export function Attachments({
  target,
  items: given,
  allowed,
  maxMb,
  onItems,
}: {
  target: Target
  items: AttachmentInfo[]
  allowed: boolean
  maxMb: number
  onItems?: (items: AttachmentInfo[]) => void
}) {
  const { t, i18n } = useTranslation()
  const [items, setItems] = useState(given)
  const [asking, setAsking] = useState<AttachmentInfo | null>(null)
  const [problem, setProblem] = useState('')
  const [over, setOver] = useState(false)
  const uploads = useUploads(target)
  const pick = useId()
  const key = keyOf(target)

  useEffect(() => setItems(given), [given])
  useEffect(
    () =>
      onUploaded((done, info) => {
        if (done === key) setItems((before) => (before.some((each) => each.id === info.id) ? before : [...before, info]))
      }),
    [key],
  )
  useEffect(() => onItems?.(items), [items, onItems])

  const add = (files: FileList | null) => {
    if (!files || files.length === 0) return
    setProblem('')
    enqueue(target, Array.from(files), maxMb)
  }
  const drop = (event: DragEvent) => {
    event.preventDefault()
    setOver(false)
    if (allowed) add(event.dataTransfer.files)
  }
  const remove = async (item: AttachmentInfo) => {
    setAsking(null)
    try {
      await attachmentsApi.remove(item.id)
      setItems((before) => before.filter((each) => each.id !== item.id))
    } catch (error) {
      setProblem(problemText(error))
    }
  }

  return (
    <div className="space-y-2 text-sm" data-testid="attachments" onDragOver={(event) => (allowed ? (event.preventDefault(), setOver(true)) : undefined)} onDragLeave={() => setOver(false)} onDrop={drop}>
      <strong className="block text-xs font-semibold tracking-wider text-mist-500 uppercase">{t('panel.attachments')}</strong>
      {items.length > 0 && (
        <ul className="grid grid-cols-2 gap-2" data-testid="attachment-list">
          {items.map((item) => (
            <li key={item.id} className="relative flex min-w-0 flex-col gap-1 rounded-[12px] border border-ink-700 bg-ink-900 p-2" data-testid="attachment">
              <a href={fileUrl(item.id, !item.inline)} target={item.inline ? '_blank' : undefined} rel="noopener noreferrer" aria-label={t(item.inline ? 'attachments.open' : 'attachments.download', { name: item.name })} className="block">
                {item.thumb ? (
                  <img src={thumbUrl(item.id)} alt="" className="h-20 w-full rounded-md bg-ink-800 object-cover" />
                ) : (
                  <span className="flex h-20 w-full items-center justify-center rounded-md bg-ink-800 text-mist-500">
                    {item.kind === 'pdf' ? <FileText aria-hidden className="h-7 w-7" /> : <FileIcon aria-hidden className="h-7 w-7" />}
                  </span>
                )}
              </a>
              <span className="truncate text-[13px] text-mist-100" title={item.name} data-testid="attachment-name">
                {item.name}
              </span>
              <span className="text-[11.5px] text-mist-500">
                {[sizeText(item.size, i18n.language), item.pages ? t('attachments.pages', { count: item.pages }) : ''].filter(Boolean).join(' · ')}
              </span>
              {allowed && (
                <button type="button" aria-label={t('attachments.remove', { name: item.name })} className="absolute top-1 right-1 rounded-full bg-ink-850/90 p-1 text-mist-400 hover:text-mist-100" onClick={() => setAsking(item)} data-testid="attachment-remove">
                  <X aria-hidden className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {uploads.map((upload) => (
        <UploadRow key={upload.id} upload={upload} language={i18n.language} />
      ))}
      {allowed && (
        <div className={'rounded-[12px] border border-dashed px-3 py-4 text-center text-[13.5px] text-mist-400 ' + (over ? 'border-accent-500 bg-[var(--color-accent-wash)]' : 'border-ink-600')} data-testid="attachment-drop">
          {t('attachments.drop')}{' '}
          <label htmlFor={pick} className="cursor-pointer text-accent-400 hover:underline">
            {t('attachments.choose')}
          </label>
          <input id={pick} type="file" multiple className="sr-only" onChange={(event) => (add(event.target.files), (event.target.value = ''))} data-testid="attachment-input" />
        </div>
      )}
      <p className="text-xs text-mist-500" data-testid="attachment-guests">
        {t('attachments.guests')}
      </p>
      {problem && (
        <p role="alert" className="text-xs text-bad-500">
          {problem}
        </p>
      )}
      {asking && (
        <Dialog title={t('attachments.removeTitle')} onClose={() => setAsking(null)}>
          <div className="space-y-4" data-testid="attachment-remove-question">
            <p className="text-sm text-mist-200">{t('attachments.removeText', { name: asking.name })}</p>
            <div className="flex justify-end gap-2">
              <button type="button" className="nt-btn nt-btn-ghost" onClick={() => setAsking(null)}>
                {t('common.cancel')}
              </button>
              <button type="button" className="nt-btn nt-btn-danger" onClick={() => void remove(asking)} data-testid="attachment-remove-yes">
                {t('attachments.removeYes')}
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  )
}

function UploadRow({ upload, language }: { upload: Upload; language: string }) {
  const { t } = useTranslation()
  if (upload.state === 'too_large')
    return (
      <div role="alert" className="flex items-start justify-between gap-3 rounded-[9px] border border-bad-500/30 bg-bad-500/10 px-3 py-2 text-[13px] text-bad-500" data-testid="upload-too-large">
        <span className="min-w-0 break-words">{t('attachments.tooLarge', { name: upload.name, size: sizeText(upload.size, language), max: Number(upload.values?.max_mb ?? upload.max ?? 0) })}</span>
        <button type="button" className="nt-btn nt-btn-quiet shrink-0 text-[13px]" onClick={() => cancelUpload(upload.id)} data-testid="upload-hide">
          {t('attachments.hide')}
        </button>
      </div>
    )
  if (upload.state === 'failed')
    return (
      <div role="alert" className="flex flex-col gap-1.5 rounded-[9px] border border-bad-500/30 bg-bad-500/10 px-3 py-2 text-[13px] text-bad-500" data-testid="upload-failed">
        <span className="break-words">{failure(upload, t)}</span>
        <span className="flex gap-3">
          {(upload.error === 'network' || upload.error === 'upload_broken') && (
            <button type="button" className="nt-btn nt-btn-quiet text-[13px]" onClick={() => retryUpload(upload.id)} data-testid="upload-retry">
              {t('attachments.retry')}
            </button>
          )}
          <button type="button" className="nt-btn nt-btn-quiet text-[13px]" onClick={() => cancelUpload(upload.id)} data-testid="upload-hide">
            {t('attachments.hide')}
          </button>
        </span>
      </div>
    )
  const percent = Math.round(upload.progress * 100)
  return (
    <div className="flex flex-col gap-1 rounded-[9px] border border-ink-700 px-3 py-2" data-testid="upload">
      <span className="flex gap-2 text-[13.5px]">
        <span className="min-w-0 flex-1 truncate" title={upload.name}>
          {upload.name}
        </span>
        <button type="button" className="nt-btn nt-btn-quiet shrink-0 text-[13px]" onClick={() => cancelUpload(upload.id)} data-testid="upload-cancel">
          {t('attachments.cancel')}
        </button>
      </span>
      <span className="h-1.5 overflow-hidden rounded-full bg-ink-700" role="progressbar" aria-label={upload.name} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <span className="block h-full bg-accent-500" style={{ width: `${percent}%` }} />
      </span>
      <span className="text-[11.5px] text-mist-500" data-testid="upload-percent">
        {upload.state === 'waiting' ? t('attachments.waiting') : `${percent} %`}
      </span>
    </div>
  )
}

function failure(upload: Upload, t: (key: string, values?: Record<string, unknown>) => string): string {
  if (upload.error === 'network' || upload.error === 'upload_broken') return t('attachments.failed', { name: upload.name })
  if (upload.error === 'proxy_too_large') return `${upload.name}: ${t('attachments.proxy')}`
  return `${upload.name}: ${errorText(upload.error ?? 'internal_error', upload.values)}`
}

/** The files an event of iCloud, Google or a CalDAV server has: shown, never added to (AC-5); fetched through nextime
 * where the source keeps them, a Google Drive link in a new tab; one that cannot be had says so (EC-14). */
export function RemoteAttachments({ eventId, items, source }: { eventId: number; items: RemoteAttachment[]; source: string }) {
  const { t } = useTranslation()
  const [missing, setMissing] = useState<string[]>([])
  const fetchOne = async (item: RemoteAttachment) => {
    try {
      const answer = await fetch(remoteUrl(eventId, item.index), { credentials: 'same-origin' })
      if (!answer.ok) throw new Error(String(answer.status))
      const blob = await answer.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = item.name
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch {
      setMissing((before) => (before.includes(item.name) ? before : [...before, item.name]))
    }
  }
  return (
    <div className="space-y-2 text-sm" data-testid="attachments-remote">
      <strong className="block text-xs font-semibold tracking-wider text-mist-500 uppercase">{t('panel.attachments')}</strong>
      {items.length > 0 && (
        <ul className="space-y-1">
          {items.map((item) =>
            item.link && source === 'google' ? (
              <li key={item.index}>
                <a href={item.link} target="_blank" rel="noopener noreferrer" className="block truncate text-accent-400 hover:underline" title={item.name} data-testid="remote-attachment">
                  {item.name}
                </a>
              </li>
            ) : (
              <li key={item.index}>
                <button type="button" className="block max-w-full truncate text-left text-accent-400 hover:underline" title={item.name} onClick={() => void fetchOne(item)} data-testid="remote-attachment">
                  {item.name}
                </button>
              </li>
            ),
          )}
        </ul>
      )}
      {missing.map((name) => (
        <p key={name} role="status" className="text-xs text-warn-500" data-testid="remote-missing">
          {t('attachments.unavailable', { name, source: t(`attachments.sourceNames.${source || 'caldav'}`) })}
        </p>
      ))}
      <p className="text-xs text-mist-500" data-testid="attachments-own-only">
        {t('attachments.ownOnly')}
      </p>
    </div>
  )
}
