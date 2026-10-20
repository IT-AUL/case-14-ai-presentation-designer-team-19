import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import styles from './RunNotice.module.css'

interface RunNoticeProps {
  tone: 'error' | 'neutral' | 'info'
  title: string
  message: ReactNode
  meta?: string | null
  details?: string | null
  actions?: ReactNode
}

export function RunNotice({ tone, title, message, meta, details, actions }: RunNoticeProps) {
  return (
    <section className={clsx(styles.root, styles[tone])} role={tone === 'error' ? 'alert' : 'status'}>
      <div className={styles.body}>
        <h2 className={styles.title}>{title}</h2>
        <div className={styles.message}>{message}</div>
        {meta && !details && <div className={styles.meta}>{meta}</div>}
        {details && (
          <details className={styles.details}>
            <summary>Технические детали</summary>
            <pre className={styles.meta}>{details}</pre>
          </details>
        )}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </section>
  )
}
