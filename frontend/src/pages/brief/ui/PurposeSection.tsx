import { useEffect, useId, useRef, type KeyboardEvent } from 'react'
import { Field, TextInput } from '@/shared/ui'
import { PURPOSES } from '../model/form'
import type { PurposeKey } from '../model/form'
import styles from './BriefForm.module.css'

interface PurposeSectionProps {
  value: PurposeKey | null
  customPurpose: string
  error?: string
  focusSignal?: number
  onChange: (purpose: PurposeKey) => void
  onCustomPurposeChange: (value: string) => void
}

const NEXT_KEYS: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }

export function PurposeSection({ value, customPurpose, error, focusSignal = 0, onChange, onCustomPurposeChange }: PurposeSectionProps) {
  const titleId = useId()
  const errorId = useId()
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const selectedIndex = PURPOSES.findIndex((option) => option.value === value)
  const hasError = Boolean(error)

  useEffect(() => {
    if (focusSignal > 0 && hasError) buttons.current[0]?.focus()
  }, [focusSignal, hasError])

  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = NEXT_KEYS[event.key]
    if (!step) return
    event.preventDefault()
    const from = selectedIndex < 0 ? 0 : selectedIndex
    const next = (from + step + PURPOSES.length) % PURPOSES.length
    const option = PURPOSES[next]
    if (!option) return
    onChange(option.value)
    buttons.current[next]?.focus()
  }

  return (
    <section className={styles.section} aria-labelledby={titleId}>
      <div className={styles.sectionHead}>
        <h2 id={titleId} className={styles.sectionTitle}>
          Назначение
        </h2>
        <span className={styles.required}>обязательно</span>
      </div>
      <div
        role="radiogroup"
        aria-labelledby={titleId}
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
        className={styles.purposes}
        onKeyDown={move}
      >
        {PURPOSES.map((option, index) => {
          const checked = option.value === value
          const focusable = checked || (selectedIndex < 0 && index === 0)
          return (
            <button
              key={option.value}
              ref={(node) => {
                buttons.current[index] = node
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={focusable ? 0 : -1}
              className={styles.purpose}
              onClick={() => onChange(option.value)}
            >
              <span className={styles.purposeLabel}>{option.label}</span>
              <span className={styles.purposeHint}>{option.hint}</span>
            </button>
          )
        })}
      </div>
      {value === 'other' && (
        <Field label="Что за презентация" hint="необязательно">
          {(id) => (
            <TextInput
              id={id}
              value={customPurpose}
              placeholder="Например: отчёт для совета директоров"
              onChange={(event) => onCustomPurposeChange(event.target.value)}
            />
          )}
        </Field>
      )}
      {error && (
        <div id={errorId} className={styles.error} role="alert">
          {error}
        </div>
      )}
    </section>
  )
}
