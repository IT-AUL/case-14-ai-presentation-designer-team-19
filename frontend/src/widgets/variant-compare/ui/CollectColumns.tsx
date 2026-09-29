import type { ReactNode } from 'react'
import { strategyInfo, useVariantSlides, type SlideInfo, type VariantSummary } from '@/entities/variant'
import { useVariantFiles } from '@/features/variant-files'

export interface CompareColumn {
  variant: VariantSummary
  name: string
  pdfUrl: string | null
  slides: SlideInfo[] | undefined
  isPending: boolean
  error: Error | null
}

type RenderColumns = (columns: CompareColumn[]) => ReactNode

function WithColumn({ variant: listed, children }: { variant: VariantSummary; children: (column: CompareColumn) => ReactNode }) {
  const files = useVariantFiles(listed.id)
  const variant = files.variant ?? listed
  const ready = variant.status === 'completed'
  const slides = useVariantSlides(ready ? variant.id : null)
  return children({
    variant,
    name: strategyInfo(variant.strategy).name,
    pdfUrl: files.pdfUrl,
    slides: ready ? slides.data : [],
    isPending: files.isPending || (ready && slides.isPending),
    error: files.error ?? slides.error,
  })
}

interface CollectColumnsProps {
  variants: readonly VariantSummary[]
  collected?: CompareColumn[]
  children: RenderColumns
}

export function CollectColumns({ variants, collected = [], children }: CollectColumnsProps) {
  const [head, ...rest] = variants
  if (!head) return children(collected)
  return (
    <WithColumn variant={head}>
      {(column) => (
        <CollectColumns variants={rest} collected={[...collected, column]}>
          {children}
        </CollectColumns>
      )}
    </WithColumn>
  )
}
