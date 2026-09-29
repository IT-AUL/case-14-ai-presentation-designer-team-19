import * as Dialog from '@radix-ui/react-dialog'
import { useState, type FormEvent } from 'react'
import { useCreateProject, type Project } from '@/entities/project'
import { isApiError } from '@/shared/api'
import { Button, Field, TextInput } from '@/shared/ui'
import { defaultProjectName, normalizeProjectName, PROJECT_NAME_MAX } from '../lib/projectName'
import styles from './CreateProjectDialog.module.css'

interface CreateProjectDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (project: Project) => void
}

function CreateProjectForm({ onCreated, onCancel }: { onCreated: (project: Project) => void; onCancel: () => void }) {
  const [name, setName] = useState(() => defaultProjectName('Новый проект'))
  const create = useCreateProject()
  const normalized = normalizeProjectName(name)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!normalized || create.isPending) return
    create.mutate(normalized, { onSuccess: onCreated })
  }

  const error = create.error ? (isApiError(create.error) ? create.error.message : 'Не удалось создать проект.') : null

  return (
    <form className={styles.form} onSubmit={submit}>
      <Field label="Название" hint="видно в списке проектов">
        {(id) => (
          <TextInput
            id={id}
            value={name}
            maxLength={PROJECT_NAME_MAX}
            autoFocus
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => setName(event.target.value)}
            aria-invalid={!normalized || undefined}
          />
        )}
      </Field>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <Button size="lg" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" variant="primary" size="lg" disabled={!normalized || create.isPending}>
          {create.isPending ? 'Создаю…' : 'Создать и загрузить шаблон'}
        </Button>
      </div>
    </form>
  )
}

export function CreateProjectDialog({ open, onOpenChange, onCreated }: CreateProjectDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content className={styles.content}>
          <Dialog.Title className={styles.title}>Новый проект</Dialog.Title>
          <Dialog.Description className={styles.description}>
            Следующий шаг — корпоративный шаблон .pptx или прошлая презентация.
          </Dialog.Description>
          <CreateProjectForm onCreated={onCreated} onCancel={() => onOpenChange(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
