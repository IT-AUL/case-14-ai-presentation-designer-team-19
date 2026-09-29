import { clsx } from 'clsx'
import type { MouseEvent } from 'react'
import styles from './Checkbox.module.css'
import { Check, Icon } from '../icon'

interface CheckboxProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  visibleLabel?: boolean
  size?: 'sm' | 'md'
  disabled?: boolean
}

export function Checkbox({ checked, onChange, label, visibleLabel, size = 'md', disabled }: CheckboxProps) {
  const toggle = (event: MouseEvent) => {
    event.stopPropagation()
    onChange(!checked)
  }
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={visibleLabel ? undefined : label}
      disabled={disabled}
      onClick={toggle}
      className={clsx(styles.root, styles[size])}
    >
      <span className={styles.box} aria-hidden>
        {checked ? <Icon as={Check} size={12} strokeWidth={3} /> : null}
      </span>
      {visibleLabel && <span className={styles.label}>{label}</span>}
    </button>
  )
}
