import { clsx } from 'clsx'
import type { HTMLAttributes, ReactNode } from 'react'
import styles from './Card.module.css'

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  padding?: 'none' | 'md' | 'lg'
  emphasis?: boolean
}

export function Card({ title, meta, actions, padding = 'md', emphasis, className, children, ...rest }: CardProps) {
  return (
    <section className={clsx(styles.card, styles[padding], emphasis && styles.emphasis, className)} {...rest}>
      {(title || meta || actions) && (
        <header className={styles.header}>
          {title && <h2 className={styles.title}>{title}</h2>}
          {meta && <div className={styles.meta}>{meta}</div>}
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}
