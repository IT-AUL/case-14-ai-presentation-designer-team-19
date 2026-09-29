import { useState, type FormEvent } from 'react'
import { SUGGESTED_MODELS } from '@/entities/provider-session'
import type { ModelRole } from '@/entities/provider-session'
import { Button, Field, TextInput } from '@/shared/ui'
import { validateProviderForm } from '../model/form'
import type { ProviderForm, ProviderFormErrors, ProviderFormField } from '../model/form'
import styles from './ProviderPanel.module.css'

interface ProviderFormViewProps {
  initial: ProviderForm
  submitting: boolean
  error: string | null
  replacing: boolean
  onSubmit: (form: ProviderForm) => void
  onCancel?: () => void
}

interface ModelInputProps {
  label: string
  role: ModelRole
  value: string
  error?: string
  optional?: boolean
  onChange: (value: string) => void
}

function ModelInput({ label, role, value, error, optional, onChange }: ModelInputProps) {
  const suggestion = SUGGESTED_MODELS[role]
  return (
    <Field label={label} hint={optional ? 'необязательно' : undefined}>
      {(id) => (
        <>
          <TextInput
            id={id}
            mono
            className={styles.codeInput}
            value={value}
            placeholder={suggestion.id}
            autoComplete="off"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            aria-describedby={`${id}-note`}
            onChange={(event) => onChange(event.target.value)}
          />
          <span id={`${id}-note`} className={error ? styles.fieldError : styles.fieldNote} role={error ? 'alert' : undefined}>
            {error ?? (
              <>
                Например,{' '}
                <a href={suggestion.url} target="_blank" rel="noreferrer">
                  {suggestion.id}
                </a>{' '}
                · {suggestion.license} · {suggestion.parametersB}B
              </>
            )}
          </span>
        </>
      )}
    </Field>
  )
}

export function ProviderFormView({ initial, submitting, error, replacing, onSubmit, onCancel }: ProviderFormViewProps) {
  const [form, setForm] = useState<ProviderForm>(initial)
  const [errors, setErrors] = useState<ProviderFormErrors>({})
  const [touched, setTouched] = useState(false)

  const set = (field: ProviderFormField) => (value: string) => {
    const next = { ...form, [field]: value }
    setForm(next)
    if (touched) setErrors(validateProviderForm(next))
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const nextErrors = validateProviderForm(form)
    setTouched(true)
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length === 0) onSubmit(form)
  }

  return (
    <form className={styles.form} onSubmit={submit} noValidate aria-label="Подключение провайдера моделей">
      <Field label="Название" hint="необязательно">
        {(id) => <TextInput id={id} value={form.label} placeholder="Например: VK Inference" onChange={(event) => set('label')(event.target.value)} />}
      </Field>
      <Field label="Адрес API">
        {(id) => (
          <>
            <TextInput
              id={id}
              mono
              className={styles.codeInput}
              type="url"
              value={form.baseUrl}
              placeholder="https://inference.example/v1"
              autoComplete="off"
              spellCheck={false}
              aria-invalid={errors.baseUrl ? true : undefined}
              aria-describedby={`${id}-note`}
              onChange={(event) => set('baseUrl')(event.target.value)}
            />
            <span id={`${id}-note`} className={errors.baseUrl ? styles.fieldError : styles.fieldNote} role={errors.baseUrl ? 'alert' : undefined}>
              {errors.baseUrl ?? 'OpenAI-совместимый endpoint, например VK Inference или vLLM'}
            </span>
          </>
        )}
      </Field>
      <ModelInput label="Модель для текста" role="text" value={form.textModel} error={errors.textModel} onChange={set('textModel')} />
      <ModelInput label="Модель для картинок слайдов" role="vision" value={form.visionModel} error={errors.visionModel} onChange={set('visionModel')} />
      <Field label="Токен">
        {(id) => (
          <>
            <TextInput
              id={id}
              type="password"
              value={form.apiToken}
              placeholder={replacing ? 'Вставьте токен ещё раз' : 'Вставьте токен'}
              autoComplete="off"
              aria-invalid={errors.apiToken ? true : undefined}
              aria-describedby={`${id}-note`}
              onChange={(event) => set('apiToken')(event.target.value)}
            />
            <span id={`${id}-note`} className={errors.apiToken ? styles.fieldError : styles.fieldNote} role={errors.apiToken ? 'alert' : undefined}>
              {errors.apiToken ?? 'Не сохраняется в браузере.'}
            </span>
          </>
        )}
      </Field>
      <details className={styles.more} open={Boolean(initial.embeddingModel || initial.imageModel || errors.embeddingModel || errors.imageModel)}>
        <summary className={styles.moreSummary}>Дополнительные модели</summary>
        <div className={styles.moreBody}>
          <ModelInput label="Эмбеддинги" role="embedding" optional value={form.embeddingModel} error={errors.embeddingModel} onChange={set('embeddingModel')} />
          <ModelInput label="Генерация изображений" role="image" optional value={form.imageModel} error={errors.imageModel} onChange={set('imageModel')} />
        </div>
      </details>
      {error && (
        <p className={styles.formError} role="alert">
          {error}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit" variant="primary" size="lg" disabled={submitting}>
          {submitting ? 'Подключаю…' : replacing ? 'Переподключить и проверить' : 'Подключить и проверить'}
        </Button>
        {onCancel && (
          <Button variant="ghost" size="lg" onClick={onCancel} disabled={submitting}>
            Отмена
          </Button>
        )}
      </div>
    </form>
  )
}
