import { clsx } from 'clsx'
import { useVariantFiles } from '@/features/variant-files'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { slidePreviewUrl, useVariantSlides, type VariantSummary } from '@/entities/variant'
import { formatClock } from '@/shared/lib/format'
import { PdfPage } from '@/shared/ui'
import { variantElapsedSeconds, variantStageLabel, type VariantCard } from '../lib/variants'
import styles from './VariantTabs.module.css'

function Cover({ variant, name }: { variant: VariantSummary; name: string }) {
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  const { pdfUrl } = useVariantFiles(variant.id)
  const { data: slides } = useVariantSlides(variant.id)
  const first = slides?.[0]
  if (!first && !pdfUrl) return <span className={styles.cover} aria-hidden />
  return (
    <span className={clsx(styles.cover, styles.coverReady)} aria-hidden>
      <PdfPage url={pdfUrl} imageUrl={slidePreviewUrl(first, renderPreviews)} pageNumber={1} label={`${name}: обложка`} />
    </span>
  )
}

function statusText(card: VariantCard): string {
  switch (card.state) {
    case 'done': {
      const seconds = variantElapsedSeconds(card.variant, Date.now())
      return seconds === null ? 'Готово' : `Готово · ${formatClock(seconds)}`
    }
    case 'running':
      return variantStageLabel(card.variant) ?? 'Собирается'
    case 'pending':
      return 'Готовится'
    case 'queued':
      return 'В очереди'
    case 'canceled':
      return 'Отменён'
    case 'error':
      return 'Не собран'
  }
}

interface VariantTabsProps {
  cards: readonly VariantCard[]
  selectedKey: string | null
  onSelect: (key: string) => void
}

export function VariantTabs({ cards, selectedKey, onSelect }: VariantTabsProps) {
  return (
    <div className={styles.tabs} role="group" aria-label="Варианты колоды">
      {cards.map((card) => {
        const selected = card.key === selectedKey
        const status = statusText(card)
        return (
          <button
            key={card.key}
            type="button"
            className={clsx(styles.tab, selected && styles.selected, styles[card.state])}
            aria-pressed={selected}
            aria-label={`${card.strategy.name}: ${status}`}
            onClick={() => onSelect(card.key)}
          >
            {card.state === 'done' && card.variant ? (
              <Cover variant={card.variant} name={card.strategy.name} />
            ) : (
              <span className={clsx(styles.cover, card.state === 'running' && styles.coverBuilding)} aria-hidden />
            )}
            <span className={styles.text}>
              <span className={styles.name}>{card.strategy.name}</span>
              <span className={styles.status}>{status}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
