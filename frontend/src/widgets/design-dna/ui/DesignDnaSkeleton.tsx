import { clsx } from 'clsx'
import { Skeleton } from '@/shared/ui'
import styles from './DesignDna.module.css'

const CELLS = ['span-7', 'span-5', 'span-12'] as const

export function DesignDnaSkeleton() {
  return (
    <div className={styles.root} aria-busy aria-label="Загрузка ДНК шаблона">
      <Skeleton className={styles.skeletonStrip} />
      <Skeleton className={styles.skeletonPanel} delay={0.1} />
      <div className={styles.grid}>
        {CELLS.map((span, index) => (
          <div key={span} className={clsx(styles.cell, styles[span])}>
            <Skeleton className={styles.skeletonSection} delay={0.2 + index * 0.1} />
          </div>
        ))}
      </div>
    </div>
  )
}
