import { clsx } from 'clsx'
import type { ExportFormat } from '@/entities/passport'
import type { DeckFile } from '@/entities/variant'
import { artifactUrl } from '@/shared/api'
import { formatBytes } from '@/shared/lib/format'
import { Download, Icon } from '@/shared/ui'
import { FORMAT_LABEL } from '../model/exportState'
import { useDeckExport } from '../model/useDeckExport'
import styles from './DownloadButton.module.css'

export interface DownloadButtonProps {
  projectId: string
  variantId: string
  format: ExportFormat
  file: DeckFile | null
  currentRevision: number | null
  supported: boolean
  fileName: string
  label: string
  primary?: boolean
}

function revision(value: number | null | undefined): string {
  return value === null || value === undefined ? '' : `r${value}`
}

function meta(file: DeckFile | null): string {
  return [file?.sizeBytes != null ? formatBytes(file.sizeBytes) : null, revision(file?.deckRevision)].filter(Boolean).join(' · ')
}

export function DownloadButton({ projectId, variantId, format, file, currentRevision, supported, fileName, label, primary }: DownloadButtonProps) {
  const { state, create, markDownloaded } = useDeckExport({ projectId, variantId, format, file, currentRevision, supported })
  const fresh = state.file && !state.stale ? state.file : null
  const previous = state.file && state.stale ? state.file : null
  const buttonClass = clsx(styles.button, primary ? styles.primary : styles.secondary)

  const link = (target: DeckFile, text: string, className: string | undefined) => (
    <a className={className} href={artifactUrl(target.artifactId)} download={fileName} onClick={markDownloaded}>
      {text}
    </a>
  )

  let action
  let note: string | null = null
  if (state.status === 'creating') {
    action = (
      <button type="button" className={buttonClass} disabled aria-busy="true">
        Собираем {FORMAT_LABEL[format]}…
      </button>
    )
  } else if (state.status === 'ready' && fresh) {
    action = (
      <a className={buttonClass} href={artifactUrl(fresh.artifactId)} download={fileName} onClick={markDownloaded}>
        <Icon as={Download} size={16} />
        {label}
      </a>
    )
    note = meta(fresh)
  } else if (state.status === 'unavailable') {
    action = state.file ? (
      link(state.file, `${label} · ${revision(state.file.deckRevision)}`, buttonClass)
    ) : (
      <button type="button" className={buttonClass} disabled title={state.message ?? undefined}>
        {label}
      </button>
    )
    note = state.message
  } else {
    action = (
      <button type="button" className={buttonClass} onClick={() => create(fileName)}>
        <Icon as={Download} size={16} />
        {label}
      </button>
    )
    note = state.status === 'error' ? state.message : previous ? `соберём для ${revision(currentRevision)}` : null
  }

  return (
    <div className={styles.root} data-status={state.status} data-format={format}>
      {action}
      {(note || (previous && state.status !== 'unavailable')) && (
        <div className={clsx(styles.note, state.status === 'error' && styles.error)} role={state.status === 'error' ? 'alert' : undefined}>
          {note}
          {previous && state.status !== 'unavailable' && (
            <>
              {note ? ' · ' : ''}
              {link(previous, `прежний ${revision(previous.deckRevision)}`, styles.previous)}
            </>
          )}
        </div>
      )}
    </div>
  )
}
