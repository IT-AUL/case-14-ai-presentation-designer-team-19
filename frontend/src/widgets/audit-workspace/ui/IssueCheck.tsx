import type { SelectionState } from '@/entities/audit'
import { Check, Icon, Minus } from '@/shared/ui'
import styles from './IssueCheck.module.css'

interface IssueCheckProps {
  state: SelectionState
  label: string
  onChange?: (selected: boolean) => void
  disabledReason?: string
}

const ARIA_CHECKED = { none: false, some: 'mixed', all: true } as const

export function IssueCheck({ state, label, onChange, disabledReason }: IssueCheckProps) {
  const disabled = Boolean(disabledReason) || !onChange
  return (
    <button
      type="button"
      role="checkbox"
      className={styles.hit}
      aria-checked={ARIA_CHECKED[state]}
      aria-label={label}
      aria-disabled={disabled || undefined}
      title={disabledReason}
      onClick={(event) => {
        event.stopPropagation()
        if (!disabled) onChange?.(state !== 'all')
      }}
    >
      <span className={styles.box} aria-hidden>
        {state === 'all' && <Icon as={Check} size={12} strokeWidth={3} />}
        {state === 'some' && <Icon as={Minus} size={12} strokeWidth={3} />}
      </span>
    </button>
  )
}
