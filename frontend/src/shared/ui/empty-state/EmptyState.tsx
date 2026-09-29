import type { ReactNode } from 'react'
import styles from './EmptyState.module.css'

interface EmptyStateProps {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}

export function EmptyState({ title, description, actions }: EmptyStateProps) {
  return (
    <div className={styles.root}>
      <div className={styles.title}>{title}</div>
      {description && <p className={styles.description}>{description}</p>}
      {actions && <div className={styles.actions}>{actions}</div>}
    </div>
  )
}
