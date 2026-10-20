import { clsx } from 'clsx'
import { useId, useRef, useState, type DragEvent } from 'react'
import { Icon, Upload } from '@/shared/ui'
import { INTAKE_ACCEPT } from '../lib/kinds'
import styles from './IntakeDropzone.module.css'

interface IntakeDropzoneProps {
  onFiles: (files: File[]) => void
  disabled?: boolean
  compact?: boolean
}

function hasFiles(event: DragEvent) {
  return Array.from(event.dataTransfer.types).includes('Files')
}

export function IntakeDropzone({ onFiles, disabled, compact }: IntakeDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const hintId = useId()
  const [dragging, setDragging] = useState(false)

  const accept = (files: FileList | null) => {
    const list = files ? Array.from(files) : []
    if (list.length) onFiles(list)
  }

  const onDragOver = (event: DragEvent<HTMLButtonElement>) => {
    if (disabled || !hasFiles(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    setDragging(true)
  }

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault()
    setDragging(false)
    if (!disabled) accept(event.dataTransfer.files)
  }

  return (
    <div className={styles.root}>
      <button
        type="button"
        className={clsx(styles.zone, compact && styles.compact, dragging && styles.dragging)}
        onClick={() => inputRef.current?.click()}
        onDragEnter={onDragOver}
        onDragOver={onDragOver}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        disabled={disabled}
        aria-describedby={compact ? undefined : hintId}
      >
        <span className={styles.lead}>
          <Icon as={Upload} size={18} />
          <span className={styles.title}>{dragging ? 'Отпустите файлы' : 'Перетащите файлы сюда'}</span>
        </span>
        <span className={styles.subtitle}>или нажмите, чтобы выбрать — можно сразу несколько</span>
        {!compact && (
          <span className={styles.formats} id={hintId}>
            Шаблон (<span className={styles.mono}>.pptx, .potx</span>) · Данные (<span className={styles.mono}>.csv, .xlsx, .json</span>) · Текст и старые слайды (<span className={styles.mono}>.docx, .pdf, .md</span>) · Брендбук (<span className={styles.mono}>tokens.json, .pdf, .png, .jpg, .webp</span>)
          </span>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={INTAKE_ACCEPT}
        multiple
        className={styles.input}
        tabIndex={-1}
        aria-hidden
        onChange={(event) => {
          accept(event.target.files)
          event.target.value = ''
        }}
      />
    </div>
  )
}
