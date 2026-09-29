import { useId, useRef, useState, type DragEvent } from 'react'
import { formatBytes } from '@/shared/lib/format'
import { Badge, Button, Icon, Segmented, TextArea, X } from '@/shared/ui'
import { CONTENT_EXTENSIONS, fileExtension, partitionContentFiles } from '../lib/brief'
import type { ContentPlan } from '../lib/brief'
import type { ContentMode, ParsedContent } from '../model/form'
import styles from './BriefForm.module.css'

const MODES = [
  { value: 'file', label: 'Файл' },
  { value: 'text', label: 'Вставить текст' },
] as const

const ACCEPT = CONTENT_EXTENSIONS.join(',')
const EXTENSIONS_LABEL = CONTENT_EXTENSIONS.join(' ')

export interface ContentSectionProps {
  mode: ContentMode
  files: File[]
  text: string
  parsed: ParsedContent | null
  plan: ContentPlan
  parsing: boolean
  parseError: string | null
  error?: string
  onModeChange: (mode: ContentMode) => void
  onFilesChange: (files: File[]) => void
  onTextChange: (text: string) => void
  onParseText: () => void
}

function FileIcon({ name }: { name: string }) {
  return (
    <span className={styles.fileIcon} aria-hidden="true">
      {fileExtension(name).slice(0, 4) || 'DOC'}
    </span>
  )
}

function parsedStatus(parsing: boolean, parseError: string | null, ready: boolean): { text: string; tone: 'meta' | 'error' } {
  if (parsing) return { text: 'разбираю…', tone: 'meta' }
  if (parseError) return { text: parseError, tone: 'error' }
  return { text: ready ? 'разобран' : 'будет разобран при сборке', tone: 'meta' }
}

export function ContentSection(props: ContentSectionProps) {
  const { mode, files, text, parsed, plan, parsing, parseError, error, onModeChange, onFilesChange, onTextChange, onParseText } = props
  const titleId = useId()
  const errorId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const { primary, ignored, unsupported } = partitionContentFiles(files)

  const addFiles = (incoming: FileList | null) => {
    if (!incoming || incoming.length === 0) return
    onFilesChange([...Array.from(incoming), ...files.filter((file) => !Array.from(incoming).some((next) => next.name === file.name))])
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    addFiles(event.dataTransfer.files)
  }

  const onDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(true)
  }

  const remove = (target: File) => onFilesChange(files.filter((file) => file !== target))
  const openPicker = () => inputRef.current?.click()
  const ready = plan.kind === 'ready'
  const status = parsedStatus(parsing, parseError, ready)
  const fallback = !primary && parsed

  return (
    <section className={styles.section} aria-labelledby={titleId}>
      <div className={styles.sectionHead}>
        <h2 id={titleId} className={styles.sectionTitle}>
          Контент
        </h2>
        <span className={styles.required}>обязательно</span>
        <div className={styles.spacer} />
        <Segmented options={MODES} value={mode} onChange={onModeChange} label="Источник контента" size="sm" />
      </div>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className={styles.fileInput}
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          addFiles(event.target.files)
          event.target.value = ''
        }}
      />

      {mode === 'file' ? (
        <div className={styles.contentBody} onDragOver={onDragOver} onDragLeave={() => setDragging(false)} onDrop={onDrop}>
          {primary && (
            <div className={styles.fileCard}>
              <FileIcon name={primary.name} />
              <div className={styles.fileText}>
                <span className={styles.fileName}>{primary.name}</span>
                <span className={status.tone === 'error' ? styles.fileError : styles.fileMeta}>
                  {formatBytes(primary.size)} · {status.text}
                </span>
              </div>
              {ready && <Badge tone="ok">используется</Badge>}
              <button type="button" className={styles.iconButton} aria-label={`Убрать файл ${primary.name}`} onClick={() => remove(primary)}>
                <Icon as={X} size={14} />
              </button>
            </div>
          )}
          {fallback && (
            <div className={styles.fileCard}>
              <FileIcon name={parsed.label} />
              <div className={styles.fileText}>
                <span className={styles.fileName}>{parsed.label}</span>
                <span className={styles.fileMeta}>{parsed.sizeBytes !== null ? `${formatBytes(parsed.sizeBytes)} · разобран` : 'разобран ранее'}</span>
              </div>
              <Badge tone="ok">используется</Badge>
            </div>
          )}
          {ignored.map((file) => (
            <div key={`${file.name}-${file.lastModified}`} className={styles.fileCard} data-muted="true">
              <FileIcon name={file.name} />
              <div className={styles.fileText}>
                <span className={styles.fileName}>{file.name}</span>
                <span className={styles.fileWarn}>Не будет учтён: используется только первый файл.</span>
              </div>
              <button type="button" className={styles.iconButton} aria-label={`Убрать файл ${file.name}`} onClick={() => remove(file)}>
                <Icon as={X} size={14} />
              </button>
            </div>
          ))}
          {unsupported.map((file) => (
            <div key={`${file.name}-${file.lastModified}`} className={styles.fileCard} data-muted="true">
              <FileIcon name={file.name} />
              <div className={styles.fileText}>
                <span className={styles.fileName}>{file.name}</span>
                <span className={styles.fileWarn}>Формат не поддерживается. Подойдут {EXTENSIONS_LABEL}</span>
              </div>
              <button type="button" className={styles.iconButton} aria-label={`Убрать файл ${file.name}`} onClick={() => remove(file)}>
                <Icon as={X} size={14} />
              </button>
            </div>
          ))}
          {!primary && !fallback ? (
            <div className={styles.dropzone} data-active={dragging}>
              <span className={styles.dropTitle}>Перетащите файл с контентом</span>
              <span className={styles.fileMeta}>{EXTENSIONS_LABEL} · разбирается один файл</span>
              <Button variant="secondary" size="md" onClick={openPicker} aria-describedby={error ? errorId : undefined}>
                Выбрать файл
              </Button>
            </div>
          ) : (
            <div className={styles.contentHint} data-active={dragging}>
              <span>{EXTENSIONS_LABEL} — перетащите ещё или</span>
              <button type="button" className={styles.linkButton} onClick={openPicker}>
                замените
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className={styles.contentBody}>
          <TextArea
            aria-label="Текст контента"
            aria-describedby={error ? errorId : undefined}
            rows={7}
            className={styles.textarea}
            value={text}
            placeholder="О чём продукт, какие есть цифры и выводы. Заголовки # и ## станут разделами."
            onChange={(event) => onTextChange(event.target.value)}
          />
          <div className={styles.textRow}>
            <Button variant="secondary" size="sm" disabled={plan.kind !== 'upload' || parsing} onClick={onParseText}>
              {parsing ? 'Разбираю…' : 'Разобрать текст'}
            </Button>
            <span className={parseError ? styles.fileError : styles.fileMeta}>
              {parseError ?? (ready && text.trim() ? 'Текст разобран' : null)}
            </span>
          </div>
        </div>
      )}
      {error && (
        <div id={errorId} className={styles.error} role="alert">
          {error}
        </div>
      )}
    </section>
  )
}
