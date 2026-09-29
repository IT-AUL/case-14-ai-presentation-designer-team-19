import { clsx } from 'clsx'
import { useVariantFiles } from '@/features/variant-files'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { DEFAULT_STRATEGY, slidePreviewUrl, strategyInfo, telltaleSlides, useVariantSlides, type VariantSummary } from '@/entities/variant'
import { PdfPage } from '@/shared/ui'
import styles from './VariantPicker.module.css'

function Cover({ variant }: { variant: VariantSummary }) {
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  const { pdfUrl } = useVariantFiles(variant.id)
  const { data: slides } = useVariantSlides(variant.id)
  const [cover] = telltaleSlides(slides ?? [], 1)
  if (!cover) return <span className={styles.cover} />
  return (
    <span className={styles.cover}>
      <PdfPage url={pdfUrl} imageUrl={slidePreviewUrl(cover, renderPreviews)} pageNumber={cover.index + 1} label="" />
    </span>
  )
}

interface VariantPickerProps {
  variants: readonly VariantSummary[]
  selectedId: string
  onSelect: (variantId: string) => void
}

export function VariantPicker({ variants, selectedId, onSelect }: VariantPickerProps) {
  return (
    <div className={styles.picker} role="radiogroup" aria-label="Вариант для экспорта">
      {variants.map((variant) => {
        const selected = variant.id === selectedId
        const name = strategyInfo(variant.strategy).name
        return (
          <button
            key={variant.id}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={name}
            className={clsx(styles.option, selected && styles.selected)}
            onClick={() => onSelect(variant.id)}
          >
            <span aria-hidden>
              <Cover variant={variant} />
            </span>
            <span className={styles.name} aria-hidden>
              {name}
              {variant.strategy === DEFAULT_STRATEGY && <span className={styles.dot} title="Рекомендуем" />}
            </span>
          </button>
        )
      })}
    </div>
  )
}
