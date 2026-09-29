import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import styles from './RunNotice.module.css'

interface RunNoticeProps {
  tone: 'error' | 'neutral' | 'info'
  title: string
  message: ReactNode
  meta?: string | null
  actions?: ReactNode
}

export function RunNotice({ tone, title, message, meta, actions }: RunNoticeProps) {
  return (
    <section className={clsx(styles.root, styles[tone])} role={tone === 'error' ? 'alert' : 'status'}>
      <div className={styles.body}>
        <h2 className={styles.title}>{title}</h2>
        <div className={styles.message}>{message}</div>
        {meta && <div className={styles.meta}>{meta}</div>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </section>
  )
}
