import { slidePurposeLabel } from '@/entities/generation'
import type { SlideInfo } from '@/entities/variant'

export function slideTitle(slide: Pick<SlideInfo, 'title' | 'purpose'>): string | null {
  return slide.title?.trim() || slidePurposeLabel(slide.purpose)
}

export function slideLabel(slide: Pick<SlideInfo, 'index' | 'title' | 'purpose'>, variantName: string): string {
  const title = slideTitle(slide)
  const base = `«${variantName}», слайд ${slide.index + 1}`
  return title ? `${base}: ${title}` : base
}
