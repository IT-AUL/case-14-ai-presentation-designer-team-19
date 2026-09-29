export type VariantsView = 'cards' | 'compare'

export function parseView(value: string | null): VariantsView {
  return value === 'compare' ? 'compare' : 'cards'
}

export function auditLink(auditPath: string, slideNumber?: number): string {
  if (slideNumber === undefined || !Number.isInteger(slideNumber) || slideNumber < 1) return auditPath
  return `${auditPath}?${new URLSearchParams({ slide: String(slideNumber) }).toString()}`
}
