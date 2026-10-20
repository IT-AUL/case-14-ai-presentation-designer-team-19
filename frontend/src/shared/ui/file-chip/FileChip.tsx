import { clsx } from 'clsx'
import { formatBytes } from '@/shared/lib/format'
import { Badge } from '../badge'
import { Check, FileText, Icon, X } from '../icon'
import { FILE_KIND_LABEL, FILE_KIND_OPTIONS, type FileKind } from './kinds'
import styles from './FileChip.module.css'

export interface FileChipProps {
  name: string
  sizeBytes?: number | null
  kind: FileKind
  accepted: boolean
  reason?: string | null
  statusText?: string | null
  onKindChange?: (kind: FileKind) => void
  onRemove?: () => void
  disabled?: boolean
}

export function FileChip({ name, sizeBytes, kind, accepted, reason, statusText, onKindChange, onRemove, disabled }: FileChipProps) {
  const broken = !accepted || kind === 'unknown'
  const status = statusText ?? (broken ? null : reason)
  return (
    <div className={clsx(styles.row, broken && styles.broken)}>
      <span className={styles.icon} aria-hidden>
        <Icon as={FileText} size={16} />
      </span>
      <span className={styles.name} title={name}>
        {name}
      </span>
      {sizeBytes != null && <span className={styles.size}>{formatBytes(sizeBytes)}</span>}
      {onKindChange ? (
        <select
          className={clsx(styles.kind, broken && styles.kindBroken)}
          aria-label={`Роль файла ${name}`}
          value={kind}
          disabled={disabled}
          title={!broken && reason ? reason : undefined}
          onChange={(event) => onKindChange(event.target.value as FileKind)}
        >
          {kind === 'unknown' && (
            <option value="unknown" disabled>
              {FILE_KIND_LABEL.unknown}
            </option>
          )}
          {FILE_KIND_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {FILE_KIND_LABEL[option]}
            </option>
          ))}
        </select>
      ) : (
        <Badge tone={broken ? 'error' : 'neutral'}>{FILE_KIND_LABEL[kind]}</Badge>
      )}
      {broken ? (
        <span className={styles.reason} role="alert" title={reason ?? undefined}>
          {reason ?? 'не поддерживается'}
        </span>
      ) : status ? (
        <span className={styles.status} title={status}>
          <Icon as={Check} size={12} strokeWidth={3} />
          {status}
        </span>
      ) : null}
      {onRemove && (
        <button type="button" className={styles.remove} aria-label={`Убрать ${name}`} disabled={disabled} onClick={onRemove}>
          <Icon as={X} size={14} />
        </button>
      )}
    </div>
  )
}
