import * as RadixSwitch from '@radix-ui/react-switch'
import type { ReactNode } from 'react'
import styles from './Switch.module.css'

interface SwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  label: ReactNode
  description?: ReactNode
  disabled?: boolean
}

export function Switch({ checked, onCheckedChange, label, description, disabled }: SwitchProps) {
  return (
    <label className={styles.root}>
      <RadixSwitch.Root className={styles.track} checked={checked} onCheckedChange={onCheckedChange} disabled={disabled}>
        <RadixSwitch.Thumb className={styles.thumb} />
      </RadixSwitch.Root>
      <span className={styles.text}>
        <span className={styles.label}>{label}</span>
        {description && <span className={styles.description}>{description}</span>}
      </span>
    </label>
  )
}
