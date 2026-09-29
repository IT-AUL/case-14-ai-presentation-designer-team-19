import { clsx } from 'clsx'
import { useId, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from 'react'
import styles from './Field.module.css'

interface FieldProps {
  label: ReactNode
  hint?: ReactNode
  className?: string
  children: (id: string) => ReactNode
}

export function Field({ label, hint, className, children }: FieldProps) {
  const id = useId()
  return (
    <div className={clsx(styles.field, className)}>
      <label htmlFor={id} className={styles.label}>
        {label}
        {hint && <span className={styles.hint}> · {hint}</span>}
      </label>
      {children(id)}
    </div>
  )
}

export function TextInput({ className, mono, ...rest }: InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }) {
  return <input className={clsx(styles.input, mono && styles.mono, className)} {...rest} />
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={clsx(styles.textarea, className)} {...rest} />
}
