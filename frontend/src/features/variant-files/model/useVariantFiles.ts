import { useExports } from '@/entities/passport'
import { resolveDeckFiles, useVariant, type DeckFiles, type VariantSummary } from '@/entities/variant'
import { artifactUrl } from '@/shared/api'

export interface VariantFiles {
  variant: VariantSummary | undefined
  files: DeckFiles | null
  pdfUrl: string | null
  pptxUrl: string | null
  passportArtifactId: string | null
  isPending: boolean
  error: Error | null
}

export function useVariantFiles(variantId: string | null | undefined): VariantFiles {
  const variantQuery = useVariant(variantId)
  const variant = variantQuery.data
  const exports = useExports(variant?.export_ids ?? [])
  const files = variant ? resolveDeckFiles(variant, exports.data) : null

  return {
    variant,
    files,
    pdfUrl: files?.pdf ? artifactUrl(files.pdf.artifactId) : null,
    pptxUrl: files?.pptx ? artifactUrl(files.pptx.artifactId) : null,
    passportArtifactId: files?.quality_passport?.artifactId ?? null,
    isPending: variantQuery.isPending || exports.isPending,
    error: variantQuery.error ?? exports.error,
  }
}
