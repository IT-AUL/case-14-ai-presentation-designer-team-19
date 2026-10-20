import { useRef } from 'react'
import type { Question } from '@/entities/assistant'
import { Badge, ChoiceCard } from '@/shared/ui'
import {
  CONTENT_SHORT_META,
  defaultOptionValue,
  isContentShortQuestion,
  questionTitle,
  resolveOptionNote,
  type QuestionSelection,
} from '../lib/questions'
import styles from './QuestionsPage.module.css'

interface ChoicesProps {
  question: Question
  heading: string | null
  reason: string | null
  selection: QuestionSelection | undefined
  skipped: boolean
  disabled?: boolean
  notes: readonly [string, string, string]
  onActivate?: () => void
  onOption: (value: string) => void
  onCustom: () => void
  onCustomText: (text: string) => void
  onNote: (slot: 0 | 1 | 2, text: string) => void
}

export function Choices({
  question,
  heading,
  reason,
  selection,
  skipped,
  disabled = false,
  notes,
  onActivate,
  onOption,
  onCustom,
  onCustomText,
  onNote,
}: ChoicesProps) {
  const customInputRef = useRef<HTMLInputElement>(null)
  const short = isContentShortQuestion(question)
  const custom = selection?.mode === 'custom'
  const groupLabel = heading ?? questionTitle(question)
  const isDisabled = skipped || disabled
  const customKeyNumber = question.options.length < 5 ? question.options.length + 1 : null

  const handleActivateCustom = () => {
    onCustom()
    setTimeout(() => customInputRef.current?.focus(), 0)
  }

  return (
    <fieldset
      className={styles.question}
      disabled={isDisabled}
      onFocusCapture={onActivate}
      onMouseDown={onActivate}
    >
      {heading && <legend className={styles.legend}>{heading}</legend>}
      {reason && <p className={styles.reason}>{reason}</p>}
      <div className={styles.options} role="radiogroup" aria-label={groupLabel}>
        {question.options.map((option, index) => {
          const meta = short ? CONTENT_SHORT_META[option.value as keyof typeof CONTENT_SHORT_META] : undefined
          const checked = !custom && selection?.value === option.value
          const title = meta?.title ?? option.label
          const note = meta?.description ?? resolveOptionNote(question, option)
          const keyNumber = index < 5 ? index + 1 : null
          return (
            <ChoiceCard
              key={option.value || '__empty__'}
              title={title}
              description={note}
              checked={checked}
              recommended={option.recommended}
              disabled={isDisabled}
              shortcut={keyNumber}
              badge={
                option.recommended ? (
                  <Badge tone={checked ? 'ok' : 'ink'} shape="tag">
                    советую
                  </Badge>
                ) : meta?.badge ? (
                  <Badge tone="warn" shape="tag">
                    {meta.badge}
                  </Badge>
                ) : null
              }
              onSelect={() => onOption(option.value)}
            />
          )
        })}
        {question.allow_custom && (
          <ChoiceCard
            as="div"
            title="Свой вариант"
            description="Сформулируйте своими словами — учту в плане"
            checked={custom}
            disabled={isDisabled}
            shortcut={customKeyNumber}
            onSelect={handleActivateCustom}
          >
            {custom && (
              <div className={styles.customFieldWrap}>
                <input
                  ref={customInputRef}
                  type="text"
                  className={styles.customInput}
                  value={selection?.customText ?? ''}
                  disabled={isDisabled}
                  autoFocus
                  aria-label="Свой вариант"
                  placeholder="Напишите свой вариант ответа…"
                  onChange={(event) => onCustomText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') {
                      onOption(defaultOptionValue(question))
                    }
                  }}
                />
              </div>
            )}
          </ChoiceCard>
        )}
      </div>
      {short && !custom && selection?.value === 'notes' && (
        <div className={styles.notes} role="region" aria-label="Ваши тезисы">
          <div className={styles.notesHeader}>
            <span className={styles.notesTitle}>Добавьте 1–3 коротких тезиса своими словами</span>
            <span className={styles.notesHint}>Они попадут в контент-пак и станут основой ключевых слайдов</span>
          </div>
          {([0, 1, 2] as const).map((slot) => (
            <label key={slot} className={styles.noteFieldRow}>
              <span className={styles.noteSlotLabel}>Тезис {slot + 1}</span>
              <input
                type="text"
                className={styles.customInput}
                value={notes[slot]}
                disabled={isDisabled}
                placeholder={
                  slot === 0
                    ? 'Например: Выручка выросла вдвое за счёт корпоративного сегмента'
                    : slot === 1
                      ? 'Например: Снижение оттока клиентов с 8% до 3,2%'
                      : 'Например: План масштабирования на следующий год'
                }
                aria-label={`Тезис ${slot + 1}`}
                onChange={(event) => onNote(slot, event.target.value)}
              />
            </label>
          ))}
        </div>
      )}
    </fieldset>
  )
}
