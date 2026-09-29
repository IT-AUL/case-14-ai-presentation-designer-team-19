import { clsx } from 'clsx'
import { useVariantFiles } from '@/features/variant-files'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { slidePreviewUrl, useVariantSlides, type VariantSummary } from '@/entities/variant'
import { PdfPage } from '@/shared/ui'
import type { VariantCardState } from '../lib/variants'
import styles from './SlideFan.module.css'

const FAN_SIZE = 3
const POSITIONS = ['back', 'middle', 'front'] as const

function ReadyFan({ variant, name }: { variant: VariantSummary; name: string }) {
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  const { pdfUrl } = useVariantFiles(variant.id)
  const { data: slides } = useVariantSlides(variant.id)
  const shown = (slides ?? []).slice(0, FAN_SIZE).reverse()
  if (shown.length === 0) return <SketchFan state="running" />
  return (
    <div className={styles.fan}>
      {shown.map((slide, index) => (
        <div key={slide.id} className={clsx(styles.frame, styles.ready, styles[POSITIONS[POSITIONS.length - shown.length + index] ?? 'front'])}>
          <div className={styles.appear}>
            <PdfPage url={pdfUrl} imageUrl={slidePreviewUrl(slide, renderPreviews)} pageNumber={slide.index + 1} label={`${name}: слайд ${slide.index + 1}`} />
          </div>
        </div>
      ))}
    </div>
  )
}

function SketchFan({ state }: { state: VariantCardState }) {
  const building = state === 'running' || state === 'pending'
  return (
    <div className={styles.fan} aria-hidden>
      {POSITIONS.map((position) => (
        <div
          key={position}
          className={clsx(styles.frame, styles.sketch, styles[position], position === 'front' && building && styles.building, state === 'error' && styles.failed)}
        >
          {position === 'front' && building && (
            <span className={styles.lines}>
              <span />
              <span />
              <span />
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

interface SlideFanProps {
  state: VariantCardState
  variant: VariantSummary | null
  name: string
}

export function SlideFan({ state, variant, name }: SlideFanProps) {
  if (state === 'done' && variant) return <ReadyFan variant={variant} name={name} />
  return <SketchFan state={state} />
}
