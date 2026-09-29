import { clsx } from 'clsx'
import type { ButtonHTMLAttributes } from 'react'
import styles from './Button.module.css'

type Variant = 'primary' | 'secondary' | 'ghost' | 'dashed'
type Size = 'sm' | 'md' | 'lg' | 'xl'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  block?: boolean
}

export function Button({ variant = 'secondary', size = 'md', block, className, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={clsx(styles.button, styles[variant], styles[size], block && styles.block, className)}
      {...rest}
    />
  )
}
