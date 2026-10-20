import type { ContentUnit, SlidePlan } from '../model/types'

export const EDITABLE_UNIT_KINDS: readonly ContentUnit['kind'][] = ['bullet', 'paragraph', 'number', 'label', 'quote', 'note', 'subtitle']

export function isEditableUnit(unit: Pick<ContentUnit, 'kind'>): boolean {
  return EDITABLE_UNIT_KINDS.includes(unit.kind)
}

const DIGIT = /\d/

export function hasChartData(slide: Pick<SlidePlan, 'content_units'>): boolean {
  return slide.content_units.some((unit) => unit.kind === 'chart' || unit.kind === 'number' || Boolean(unit.chart_ref) || (isEditableUnit(unit) && DIGIT.test(unit.text ?? '')))
}
