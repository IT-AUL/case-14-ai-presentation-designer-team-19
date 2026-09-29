import type { DeckPlan, SlidePlan } from '@/entities/generation'

export interface SlidePatch {
  title_intent?: string
  key_message?: string
}

function ordered(plan: DeckPlan): SlidePlan[] {
  return [...plan.slides].sort((a, b) => a.index - b.index)
}

function withSlides(plan: DeckPlan, slides: readonly SlidePlan[]): DeckPlan {
  const ids = new Set(slides.map((slide) => slide.id))
  return {
    ...plan,
    slides: slides.map((slide, index) => (slide.index === index ? slide : { ...slide, index })),
    sections: plan.sections?.map((section) => ({ ...section, slide_ids: section.slide_ids.filter((id) => ids.has(id)) })),
  }
}

export function moveSlide(plan: DeckPlan, slideId: string, delta: -1 | 1): DeckPlan {
  const slides = ordered(plan)
  const from = slides.findIndex((slide) => slide.id === slideId)
  const to = from + delta
  if (from < 0 || to < 0 || to >= slides.length) return plan
  const next = [...slides]
  const [moved] = next.splice(from, 1)
  if (moved) next.splice(to, 0, moved)
  return withSlides(plan, next)
}

export function removeSlide(plan: DeckPlan, slideId: string): DeckPlan {
  const slides = ordered(plan)
  if (slides.length <= 1 || !slides.some((slide) => slide.id === slideId)) return plan
  return withSlides(
    plan,
    slides.filter((slide) => slide.id !== slideId),
  )
}

export function updateSlide(plan: DeckPlan, slideId: string, patch: SlidePatch): DeckPlan {
  let changed = false
  const slides = ordered(plan).map((slide) => {
    if (slide.id !== slideId) return slide
    const next = { ...slide, ...patch }
    changed = next.title_intent !== slide.title_intent || next.key_message !== slide.key_message
    return next
  })
  return changed ? withSlides(plan, slides) : plan
}

function uniqueId(plan: DeckPlan): string {
  const taken = new Set(plan.slides.map((slide) => slide.id))
  let counter = plan.slides.length + 1
  while (taken.has(`slide-user-${counter}`)) counter += 1
  return `slide-user-${counter}`
}

export function addSlide(plan: DeckPlan, title = 'Новый слайд'): DeckPlan {
  const slide: SlidePlan = {
    id: uniqueId(plan),
    index: plan.slides.length,
    purpose: 'custom',
    title_intent: title,
    key_message: '',
    evidence_ids: [],
    content_units: [],
    density_budget: { level: 'medium' },
    desired_visual: 'none',
    mandatory: false,
  }
  return withSlides(plan, [...ordered(plan), slide])
}

function signature(plan: DeckPlan): string {
  return JSON.stringify(ordered(plan).map((slide) => [slide.id, slide.title_intent, slide.key_message]))
}

export function isPlanEdited(base: DeckPlan, draft: DeckPlan): boolean {
  return signature(base) !== signature(draft)
}

export function editSummary(base: DeckPlan, draft: DeckPlan): { removed: number; added: number; renamed: number; moved: boolean } {
  const before = new Map(base.slides.map((slide) => [slide.id, slide]))
  const after = new Map(draft.slides.map((slide) => [slide.id, slide]))
  const removed = [...before.keys()].filter((id) => !after.has(id)).length
  const added = [...after.keys()].filter((id) => !before.has(id)).length
  const renamed = [...after.values()].filter((slide) => {
    const original = before.get(slide.id)
    return original !== undefined && (original.title_intent !== slide.title_intent || original.key_message !== slide.key_message)
  }).length
  const kept = (plan: DeckPlan) =>
    ordered(plan)
      .map((slide) => slide.id)
      .filter((id) => before.has(id) && after.has(id))
      .join('|')
  return { removed, added, renamed, moved: kept(base) !== kept(draft) }
}
