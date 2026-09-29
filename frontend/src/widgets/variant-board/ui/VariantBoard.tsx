import { useCallback, useMemo, useState } from 'react'
import type { VariantSummary } from '@/entities/variant'
import { Skeleton } from '@/shared/ui'
import { recommendVariant, type CriticalCounts } from '../lib/recommend'
import styles from './VariantBoard.module.css'
import { VariantCard } from './VariantCard'

interface VariantBoardProps {
  variants: readonly VariantSummary[]
  showThumbnails: boolean
  auditHref: (variantId: string, slideNumber?: number) => string
}

export function VariantBoard({ variants, showThumbnails, auditHref }: VariantBoardProps) {
  const [critical, setCritical] = useState<CriticalCounts>({})
  const recommendation = useMemo(() => recommendVariant(variants, critical), [variants, critical])
  const reportCritical = useCallback((variantId: string, count: number | null) => {
    setCritical((current) => (current[variantId] === count ? current : { ...current, [variantId]: count }))
  }, [])

  return (
    <div className={styles.board}>
      {variants.map((variant) => (
        <VariantCard
          key={variant.id}
          variant={variant}
          showThumbnails={showThumbnails}
          auditHref={auditHref}
          recommendation={recommendation}
          onCriticalCount={reportCritical}
        />
      ))}
    </div>
  )
}

export function VariantBoardSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className={styles.board} aria-busy="true" aria-label="Загружаем варианты">
      {Array.from({ length: count }, (_, index) => (
        <Skeleton key={index} className={styles.skeleton} delay={index * 0.12} />
      ))}
    </div>
  )
}
