import { Field, Segmented, StepperInput, TagInput, TextInput } from '@/shared/ui'
import { LANGUAGES, SLIDE_COUNT_MAX, SLIDE_COUNT_MIN } from '../model/form'
import type { BriefForm } from '../model/form'
import styles from './BriefForm.module.css'

interface ParamsSectionProps {
  form: BriefForm
  onChange: (patch: Partial<BriefForm>) => void
}

export function ParamsSection({ form, onChange }: ParamsSectionProps) {
  return (
    <section className={styles.params} aria-label="Параметры брифа">
      <Field label="Аудитория">
        {(id) => <TextInput id={id} value={form.audience} placeholder="Кто будет смотреть" onChange={(event) => onChange({ audience: event.target.value })} />}
      </Field>
      <Field label="Тон" hint="необязательно">
        {(id) => <TextInput id={id} value={form.tone} placeholder="Например: деловой, спокойный" onChange={(event) => onChange({ tone: event.target.value })} />}
      </Field>
      <div className={styles.fieldGroup}>
        <span className={styles.fieldLabel}>Язык</span>
        <Segmented options={LANGUAGES} value={form.language} onChange={(language) => onChange({ language })} label="Язык презентации" className={styles.alignStart} />
      </div>
      <div className={styles.fieldGroup}>
        <span className={styles.fieldLabel}>
          Число слайдов{' '}
          <span className={styles.fieldHint}>
            · {SLIDE_COUNT_MIN}–{SLIDE_COUNT_MAX}
          </span>
        </span>
        <StepperInput value={form.slideCount} min={SLIDE_COUNT_MIN} max={SLIDE_COUNT_MAX} label="Число слайдов" onChange={(slideCount) => onChange({ slideCount })} />
      </div>
      <Field label="Обязательные разделы" className={styles.wide}>
        {(id) => (
          <TagInput id={id} values={form.mandatorySections} placeholder="Введите раздел и нажмите Enter" onChange={(mandatorySections) => onChange({ mandatorySections })} />
        )}
      </Field>
      <Field label="Запрещённые утверждения" hint="необязательно" className={styles.wide}>
        {(id) => (
          <TagInput id={id} values={form.forbiddenClaims} placeholder="Введите утверждение и нажмите Enter" onChange={(forbiddenClaims) => onChange({ forbiddenClaims })} />
        )}
      </Field>
    </section>
  )
}
