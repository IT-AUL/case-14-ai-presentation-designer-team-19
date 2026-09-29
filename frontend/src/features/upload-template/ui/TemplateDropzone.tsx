import { clsx } from 'clsx'
import { useId, useRef, useState, type DragEvent } from 'react'
import { pickTemplateFile, TEMPLATE_ACCEPT } from '../lib/validateTemplateFile'
import styles from './TemplateDropzone.module.css'

interface TemplateDropzoneProps {
  onFile: (file: File) => void
  error?: string | null
}

function hasFiles(event: DragEvent) {
  return Array.from(event.dataTransfer.types).includes('Files')
}

export function TemplateDropzone({ onFile, error }: TemplateDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const hintId = useId()
  const errorId = useId()
  const [dragging, setDragging] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  const shownError = localError ?? error ?? null

  const accept = (files: FileList | null) => {
    const picked = pickTemplateFile(files)
    setLocalError(picked.error)
    if (picked.file) onFile(picked.file)
  }

  const onDragOver = (event: DragEvent<HTMLButtonElement>) => {
    if (!hasFiles(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    setDragging(true)
  }

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    setDragging(false)
    accept(event.dataTransfer.files)
  }

  return (
    <div className={styles.root}>
      <button
        type="button"
        className={clsx(styles.zone, dragging && styles.dragging, shownError && styles.invalid)}
        onClick={() => inputRef.current?.click()}
        onDragEnter={onDragOver}
        onDragOver={onDragOver}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        aria-describedby={shownError ? `${hintId} ${errorId}` : hintId}
      >
        <span className={styles.title}>{dragging ? 'Отпустите файл' : 'Перетащите шаблон сюда'}</span>
        <span className={styles.subtitle}>или нажмите, чтобы выбрать файл</span>
        <span className={styles.formats} id={hintId}>
          <span className={styles.chip}>.pptx</span>
          <span className={styles.chip}>.potx</span>
          <span className={styles.limit}>до 200 МБ</span>
        </span>
        <span className={styles.note}>Подойдёт и прошлая презентация: стиль будет извлечён из обычных слайдов.</span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={TEMPLATE_ACCEPT}
        className={styles.input}
        tabIndex={-1}
        aria-hidden
        onChange={(event) => {
          accept(event.target.files)
          event.target.value = ''
        }}
      />
      {shownError && (
        <p className={styles.error} id={errorId} role="alert">
          {shownError}
        </p>
      )}
    </div>
  )
}
