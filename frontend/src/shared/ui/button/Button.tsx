import { clsx } from 'clsx'
import type { ButtonHTMLAttributes } from 'react'
import styles from './Button.module.css'

type Variant = 'primary' | 'secondary' | 'ghost' | 'dashed'
type Size = 'sm' | 'md' | 'lg' | 'xl'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  block?: boolean
  pending?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  block,
  pending = false,
  disabled,
  className,
  type = 'button',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      data-pending={pending || undefined}
      className={clsx(
        styles.button,
        styles[variant],
        styles[size],
        block && styles.block,
        pending && styles.pending,
        className,
      )}
      {...rest}
    >
      {pending && <span aria-hidden="true" className={styles.spinner} />}
      <span className={styles.label}>{children}</span>
    </button>
  )
}

export function LinkButton({ variant = 'secondary', size = 'sm', className, ...rest }: ButtonProps) {
  return <Button variant={variant} size={size} className={clsx(styles.linkButton, className)} {...rest} />
}
