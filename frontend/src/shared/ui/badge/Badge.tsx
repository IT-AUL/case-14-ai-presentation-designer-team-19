import { clsx } from 'clsx'
import type { HTMLAttributes } from 'react'
import styles from './Badge.module.css'

export type Tone = 'neutral' | 'ink' | 'ok' | 'warn' | 'error' | 'blocker' | 'info' | 'muted'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
  dot?: boolean
  shape?: 'pill' | 'tag'
  mono?: boolean
}

export function Badge({ tone = 'neutral', dot, shape = 'pill', mono, className, children, ...rest }: BadgeProps) {
  return (
    <span className={clsx(styles.badge, styles[tone], styles[shape], mono && styles.mono, className)} {...rest}>
      {dot && <span className={styles.dot} aria-hidden />}
      {children}
    </span>
  )
}
