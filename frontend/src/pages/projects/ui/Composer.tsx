import { clsx } from 'clsx'
import { useId, useRef, useState, type DragEvent, type FormEvent, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router'
import { defaultProjectName } from '@/features/create-project'
import { useCreateProject } from '@/entities/project'
import { humanError } from '@/shared/api'
import { routes } from '@/shared/config'
import { intakeHandoffState } from '@/shared/lib/intake-handoff'
import { Button, FILE_KIND_LABEL, Icon, Paperclip, X } from '@/shared/ui'
import {
  buildPresetFiles,
  buildPresetTemplateFile,
  detectActivePresetId,
  getStage2Preset,
  guessKind,
  INTAKE_ACCEPT,
  isIntakeFile,
  isPresetFileName,
  STAGE2_PRESETS,
  type Stage2PresetId,
} from '@/widgets/intake-board'
import { nameFromTopic } from '../model/projectView'
import styles from './Composer.module.css'

function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`
}

export function Composer() {
  const navigate = useNavigate()
  const create = useCreateProject()
  const inputId = useId()
  const fileInput = useRef<HTMLInputElement>(null)
  const [topic, setTopic] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [rejected, setRejected] = useState<string[]>([])
  const [dragging, setDragging] = useState(false)

  const ready = Boolean(topic.trim()) || files.length > 0
  const hasTemplate = files.some((file) => guessKind(file.name) === 'template')
  const activePresetId = detectActivePresetId(files.map((file) => file.name))

  const addFiles = (picked: File[]) => {
    if (!picked.length) return
    const supported = picked.filter((file) => isIntakeFile(file.name))
    setRejected(picked.filter((file) => !isIntakeFile(file.name)).map((file) => file.name))
    setFiles((current) => {
      const known = new Set(current.map(fileKey))
      return [...current, ...supported.filter((file) => !known.has(fileKey(file)))]
    })
  }

  const applyPreset = async (presetId: Stage2PresetId) => {
    const preset = getStage2Preset(presetId)
    if (!preset) return
    setTopic(preset.topic)
    setRejected([])
    const presetFiles = buildPresetFiles(preset.id)
    const templateFile = hasTemplate ? null : await buildPresetTemplateFile()
    setFiles((current) => {
      const kept = current.filter((file) => !isPresetFileName(file.name))
      const templateMissing = !kept.some((file) => guessKind(file.name) === 'template')
      return [...(templateFile && templateMissing ? [templateFile] : []), ...kept, ...presetFiles]
    })
  }

  const removeFile = (file: File) => setFiles((current) => current.filter((item) => item !== file))

  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    if (!ready || create.isPending) return
    const name = nameFromTopic(topic, files) ?? defaultProjectName('Новый проект')
    create.mutate(name, {
      onSuccess: (project) => navigate(routes.start(project.id), { state: intakeHandoffState({ idea: topic.trim(), files }) }),
    })
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) submit()
  }

  const onDragOver = (event: DragEvent<HTMLFormElement>) => {
    if (!event.dataTransfer.types.includes('Files')) return
    event.preventDefault()
    setDragging(true)
  }

  const onDrop = (event: DragEvent<HTMLFormElement>) => {
    event.preventDefault()
    setDragging(false)
    addFiles(Array.from(event.dataTransfer.files))
  }

  const error = create.error ? humanError(create.error, 'Не удалось создать проект.') : null

  return (
    <section className={styles.composer} aria-labelledby={`${inputId}-title`}>
      <div className={styles.intro}>
        <h1 id={`${inputId}-title`} className={styles.title}>
          Новая презентация
        </h1>
        <p className={styles.lead}>Опишите тему и прикрепите корпоративный шаблон. Соберу три редактируемых варианта в его стиле и проверю каждый слайд.</p>
      </div>
      <form
        className={clsx(styles.box, dragging && styles.dragging)}
        onSubmit={submit}
        onDragOver={onDragOver}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <label htmlFor={inputId} className={styles.srOnly}>
          О чём презентация
        </label>
        <textarea
          id={inputId}
          className={styles.topic}
          rows={3}
          value={topic}
          placeholder="Например: итоги продаж за год для совета директоров — что выросло, почему и что делаем дальше"
          onChange={(event) => setTopic(event.target.value)}
          onKeyDown={onKeyDown}
        />
        {files.length > 0 && (
          <ul className={styles.files} aria-label="Прикреплённые файлы">
            {files.map((file) => (
              <li key={fileKey(file)} className={styles.file}>
                <span className={styles.fileName}>{file.name}</span>
                <span className={styles.fileRole}>{FILE_KIND_LABEL[guessKind(file.name)]}</span>
                <button type="button" className={styles.fileRemove} aria-label={`Убрать ${file.name}`} onClick={() => removeFile(file)}>
                  <Icon as={X} size={13} />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className={styles.bar}>
          <button type="button" className={styles.attach} onClick={() => fileInput.current?.click()}>
            <Icon as={Paperclip} size={16} />
            {hasTemplate ? 'Добавить материалы' : 'Прикрепить шаблон и материалы'}
          </button>
          <span className={styles.hint}>{dragging ? 'Отпустите, чтобы прикрепить' : 'PPTX-шаблон, таблицы, тексты, брендбук · можно перетащить сюда'}</span>
          <Button type="submit" variant="primary" size="lg" disabled={!ready} pending={create.isPending}>
            Начать
          </Button>
        </div>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={INTAKE_ACCEPT}
          className={styles.srOnly}
          tabIndex={-1}
          aria-hidden
          onChange={(event) => {
            addFiles(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />
      </form>
      <div className={styles.presets} role="group" aria-label="Попробовать на данных">
        <span className={styles.presetsLabel}>Попробовать на данных:</span>
        <div className={styles.presetChips}>
          {STAGE2_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className={styles.presetChip}
              aria-pressed={activePresetId === preset.id}
              title={`${preset.title} · ${preset.summary}`}
              onClick={() => applyPreset(preset.id)}
            >
              {preset.chip}
            </button>
          ))}
        </div>
      </div>
      {(rejected.length > 0 || error) && (
        <p className={styles.error} role="alert">
          {error ?? `Не подходит формат: ${rejected.join(', ')}`}
        </p>
      )}
    </section>
  )
}
