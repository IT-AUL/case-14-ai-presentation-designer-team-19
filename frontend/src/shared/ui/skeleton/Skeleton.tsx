import { clsx } from 'clsx'
import type { CSSProperties } from 'react'
import styles from './Skeleton.module.css'

interface SkeletonProps {
  className?: string
  style?: CSSProperties
  delay?: number
}

export function Skeleton({ className, style, delay = 0 }: SkeletonProps) {
  return <div aria-hidden className={clsx(styles.skeleton, className)} style={{ animationDelay: `${delay}s`, ...style }} />
}

export function PassportSkeleton({ label = 'Читаю шаблон' }: { label?: string }) {
  return (
    <div className={styles.passport} role="status" aria-label={label}>
      <div className={styles.passportHead}>
        <div className={styles.col}>
          <Skeleton className={styles.hTitle} />
          <Skeleton className={styles.hMeta} delay={0.05} />
        </div>
        <span className={styles.statusLabel}>{label}</span>
      </div>
      <div className={styles.passportGrid}>
        <div className={styles.passportBlock}>
          <Skeleton className={styles.hSection} />
          <div className={styles.swatches}>
            {[0, 1, 2, 3, 4].map((index) => (
              <div key={index} className={styles.swatchCol}>
                <Skeleton className={styles.swatch} delay={index * 0.04} />
                <Skeleton className={styles.swatchMeta} delay={index * 0.04} />
              </div>
            ))}
          </div>
        </div>
        <div className={styles.passportBlock}>
          <Skeleton className={styles.hSection} />
          <Skeleton className={styles.hLine} delay={0.08} />
          <Skeleton className={styles.hLineShort} delay={0.12} />
        </div>
      </div>
      <div className={styles.passportBlock}>
        <Skeleton className={styles.hSection} />
        <div className={styles.roleStrip}>
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className={styles.roleCell}>
              <Skeleton className={styles.roleNum} delay={index * 0.05} />
              <Skeleton className={styles.roleLabel} delay={index * 0.05} />
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function InsightBoardSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className={styles.board} role="status" aria-label="Разбираю материалы">
      <div className={styles.datasetHead}>
        <Skeleton className={styles.hTitle} />
        <div className={styles.chips}>
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className={styles.chip} delay={index * 0.04} />
          ))}
        </div>
      </div>
      <div className={styles.stack}>
        {Array.from({ length: count }, (_, index) => (
          <div key={index} className={styles.insightCard}>
            <div className={styles.insightMain}>
              <div className={styles.col}>
                <Skeleton className={styles.hMeta} delay={index * 0.05} />
                <Skeleton className={styles.hSection} delay={index * 0.05 + 0.03} />
                <Skeleton className={styles.hLine} delay={index * 0.05 + 0.06} />
                <Skeleton className={styles.hLineShort} delay={index * 0.05 + 0.09} />
              </div>
              <Skeleton className={styles.chartBox} delay={index * 0.05 + 0.06} />
            </div>
            <div className={styles.cardFooter}>
              <Skeleton className={styles.hMeta} />
              <div className={styles.chips}>
                <Skeleton className={styles.btnPill} />
                <Skeleton className={styles.btnPill} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function DeckSpecSkeleton() {
  return (
    <div className={styles.specSheet} role="status" aria-label="Загружаю спецификацию">
      <div className={styles.passportHead}>
        <div className={styles.col}>
          <Skeleton className={styles.hMeta} />
          <Skeleton className={styles.hTitle} delay={0.04} />
        </div>
        <div className={styles.chips}>
          <Skeleton className={styles.chip} />
          <Skeleton className={styles.chip} delay={0.04} />
        </div>
      </div>
      <div className={styles.specGrid}>
        {[0, 1, 2].map((index) => (
          <div key={index} className={styles.specCard}>
            <Skeleton className={styles.hMeta} delay={index * 0.04} />
            <Skeleton className={styles.hSection} delay={index * 0.04 + 0.03} />
            <Skeleton className={styles.hLineShort} delay={index * 0.04 + 0.06} />
          </div>
        ))}
      </div>
      <div className={styles.stackTight}>
        {[0, 1, 2, 3].map((index) => (
          <div key={index} className={styles.outlineRow}>
            <Skeleton className={styles.hMeta} delay={index * 0.04} />
            <div className={styles.col}>
              <Skeleton className={styles.hSection} delay={index * 0.04 + 0.02} />
              <Skeleton className={styles.hLine} delay={index * 0.04 + 0.04} />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function ChoiceListSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className={styles.stackTight} role="status" aria-label="Загружаю варианты">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className={styles.choiceRow}>
          <Skeleton className={styles.radioCircle} delay={index * 0.04} />
          <div className={styles.col}>
            <Skeleton className={styles.hSection} delay={index * 0.04 + 0.02} />
            <Skeleton className={styles.hLineShort} delay={index * 0.04 + 0.04} />
          </div>
        </div>
      ))}
    </div>
  )
}

export function PlanBoardSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className={styles.board} role="status" aria-label="Составляю план">
      <div className={styles.passportHead}>
        <Skeleton className={styles.hMeta} />
        <Skeleton className={styles.btnPill} />
      </div>
      <div className={styles.stack}>
        {Array.from({ length: count }, (_, index) => (
          <div key={index} className={styles.planCard}>
            <div className={styles.chips}>
              <Skeleton className={styles.swatchMeta} delay={index * 0.04} />
              <Skeleton className={styles.chip} delay={index * 0.04} />
            </div>
            <Skeleton className={styles.hTitle} delay={index * 0.04 + 0.02} />
            <Skeleton className={styles.hLine} delay={index * 0.04 + 0.04} />
            <Skeleton className={styles.hLineShort} delay={index * 0.04 + 0.06} />
            <Skeleton className={styles.chartBar} delay={index * 0.04 + 0.08} />
          </div>
        ))}
      </div>
    </div>
  )
}

export function ThreeColSkeleton() {
  return (
    <div className={styles.threeCols} role="status" aria-label="Примеряю три стиля">
      {[0, 1, 2].map((col) => (
        <div key={col} className={styles.styleCol}>
          <div className={styles.choiceRowPlain}>
            <Skeleton className={styles.radioCircle} delay={col * 0.05} />
            <Skeleton className={styles.hSection} delay={col * 0.05} />
          </div>
          <Skeleton className={styles.hLine} delay={col * 0.05 + 0.03} />
          {[0, 1, 2].map((slide) => (
            <Skeleton key={slide} className={styles.slideFrame} delay={(col + slide) * 0.05} />
          ))}
        </div>
      ))}
    </div>
  )
}
