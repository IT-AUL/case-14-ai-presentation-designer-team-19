import { useParams } from 'react-router'
import { useAssistantSession, useUnderstanding, type IntakeFile, type IntakeKind } from '@/entities/assistant'
import { useContentPack } from '@/entities/content-pack'
import { useProject } from '@/entities/project'
import { FILE_KIND_LABEL, FILE_KIND_OPTIONS, Icon, X } from '@/shared/ui'
import { guessKind, isIntakeFile, type PendingFile } from '@/widgets/intake-board'
import { datasetDimensionsForFile, type DatasetMetaInput, type ExistingFile } from '../lib/existing'
import styles from './IntakePage.module.css'

const ROLE_REASON_LABEL: Record<string, string> = {
  template: 'шаблон',
  content: 'материалы',
  data: 'данные',
  brandbook: 'брендбук',
}

function formatFileReason(reason: string | null | undefined): string {
  if (!reason) return 'Не читаю такой формат'
  return reason.replace(/«(template|content|data|brandbook)»/g, (_, role: string) => `«${ROLE_REASON_LABEL[role] ?? role}»`)
}

function suggestedKind(file: IntakeFile): IntakeKind {
  if (file.kind !== 'unknown') return file.kind
  const guessed = guessKind(file.name)
  return guessed === 'unknown' ? 'content' : guessed
}

interface FileRowsProps {
  files: readonly IntakeFile[]
  pending: readonly PendingFile[]
  packId?: string | null
  datasets?: readonly DatasetMetaInput[]
  onKind: (name: string, kind: IntakeKind) => void
  onRemove: (name: string) => void
}

export function FileRows({ files, pending, packId: packIdProp, datasets: datasetsProp, onKind, onRemove }: FileRowsProps) {
  const { projectId } = useParams<{ projectId: string }>()
  const hasDataFile = files.some((file) => file.accepted && file.kind === 'data')
  const shouldFetch = datasetsProp === undefined && hasDataFile && Boolean(projectId)
  const session = useAssistantSession(shouldFetch && !packIdProp ? projectId : null)
  const project = useProject(shouldFetch && !packIdProp ? projectId : undefined)
  const packId = shouldFetch ? (packIdProp ?? session.data?.artifacts.content_pack_id ?? project.data?.content_pack_id ?? null) : null
  const contentPack = useContentPack(packId)
  const understanding = useUnderstanding(packId)
  const datasets = datasetsProp ?? (contentPack.data?.datasets?.length ? contentPack.data.datasets : understanding.data?.datasets) ?? []
  const dataFileCount = files.filter((file) => file.accepted && file.kind === 'data').length
  const known = new Set(files.map((file) => file.name))

  return (
    <ul className={styles.files} aria-label="Файлы">
      {files.map((file) => {
        const meta = file.accepted && file.kind === 'data' ? datasetDimensionsForFile(file.name, datasets, dataFileCount) : null
        const canChooseKind = file.accepted || isIntakeFile(file.name)
        const selectedKind = suggestedKind(file)
        return (
          <li key={file.name} className={styles.file} data-broken={!file.accepted || undefined}>
            <span className={styles.fileName} title={file.name}>
              {file.name}
              {meta && (
                <>
                  <br />
                  <span className={styles.fileKind}>{meta}</span>
                </>
              )}
            </span>
            {!file.accepted && (
              <span className={styles.fileError} role="alert">
                {formatFileReason(file.reason)}
              </span>
            )}
            {canChooseKind && (
              <select
                className={styles.kind}
                value={selectedKind}
                aria-label={`Что это за файл: ${file.name}`}
                onChange={(event) => onKind(file.name, event.target.value as IntakeKind)}
              >
                {FILE_KIND_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {FILE_KIND_LABEL[option]}
                  </option>
                ))}
              </select>
            )}
            <button type="button" className={styles.remove} onClick={() => onRemove(file.name)} aria-label={`Убрать ${file.name}`}>
              <Icon as={X} size={14} />
            </button>
          </li>
        )
      })}
      {pending
        .filter((file) => !known.has(file.name))
        .map((file) => (
          <li key={file.name} className={styles.file} aria-busy="true">
            <span className={styles.fileName}>{file.name}</span>
            <span className={styles.filePending}>загружаю</span>
          </li>
        ))}
    </ul>
  )
}

export function ExistingRows({ files }: { files: readonly ExistingFile[] }) {
  if (files.length === 0) return null
  return (
    <ul className={styles.files} aria-label="Уже в проекте">
      {files.map((file) => (
        <li key={`${file.kind}:${file.name}`} className={styles.file}>
          <span className={styles.fileName} title={file.name}>
            {file.name}
            {file.meta && (
              <>
                <br />
                <span className={styles.fileKind}>{file.meta}</span>
              </>
            )}
          </span>
          <span className={styles.fileKind}>{FILE_KIND_LABEL[file.kind]}</span>
        </li>
      ))}
    </ul>
  )
}
