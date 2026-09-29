import type { SlideInfo } from '../model/types'

const SERVICE_PURPOSES = new Set(['title', 'agenda', 'section_divider', 'thank_you', 'qa', 'cta'])

export function telltaleSlides(slides: readonly SlideInfo[], count: number): SlideInfo[] {
  const content = slides.filter((slide) => !SERVICE_PURPOSES.has(slide.purpose ?? ''))
  const rest = slides.filter((slide) => !content.includes(slide))
  return [...content, ...rest].slice(0, count)
}
