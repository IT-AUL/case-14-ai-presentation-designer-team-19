import type { IntakeFile, IntakeKind } from '@/entities/assistant'
import { FileChip, Skeleton } from '@/shared/ui'
import { guessKind } from '../lib/kinds'
import styles from './IntakeFileList.module.css'

export interface PendingFile {
  name: string
  size: number
}

interface IntakeFileListProps {
  files: IntakeFile[]
  onKindChange: (name: string, kind: IntakeKind) => void
  onRemove: (name: string) => void
  pending?: boolean
  uploading?: PendingFile[]
}

export function IntakeFileList({ files, onKindChange, onRemove, pending, uploading }: IntakeFileListProps) {
  if (!files.length && !uploading?.length) return null
  return (
    <ul className={styles.list} aria-label="Загруженные файлы" aria-busy={pending || undefined}>
      {files.map((file) => (
        <li key={file.name}>
          <FileChip
            name={file.name}
            sizeBytes={file.size_bytes}
            kind={file.kind === 'unknown' ? guessKind(file.name) : file.kind}
            accepted={file.accepted}
            reason={file.reason}
            disabled={pending}
            onKindChange={(kind) => onKindChange(file.name, kind as IntakeKind)}
            onRemove={() => onRemove(file.name)}
          />
        </li>
      ))}
      {uploading?.map((file) => (
        <li key={`uploading-${file.name}`} className={styles.pendingRow}>
          <span className={styles.pendingName}>{file.name}</span>
          <Skeleton className={styles.pendingBadge} />
        </li>
      ))}
    </ul>
  )
}
