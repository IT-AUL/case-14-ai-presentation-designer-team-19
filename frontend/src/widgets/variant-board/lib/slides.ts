import { slidePurposeLabel } from '@/entities/generation'
import type { SlideInfo } from '@/entities/variant'

export function slideLabel(slide: Pick<SlideInfo, 'index' | 'title' | 'purpose'>): string {
  const number = slide.index + 1
  const title = slide.title?.trim() || slidePurposeLabel(slide.purpose)
  return title ? `Слайд ${number}: ${title}` : `Слайд ${number}`
}
