import type { SufficiencyStatus } from './planExtras'

export type ContentFillMode = 'expand' | 'condense' | 'keep'

export interface ContentFillOption {
  value: ContentFillMode
  label: string
  hint: string
}

export const CONTENT_FILL_MODES: readonly ContentFillOption[] = [
  { value: 'expand', label: 'Дополнить', hint: 'недостающее найдём и предложим в плане' },
  { value: 'condense', label: 'Ужать', hint: 'сократим до нужного числа слайдов' },
  { value: 'keep', label: 'Сохранить как есть', hint: 'ваш текст не переписываем, только сокращаем' },
]

export type ServiceContentFill = 'expand' | 'shrink' | 'ask'

const SERVICE_VALUE: Record<ContentFillMode, ServiceContentFill> = { expand: 'expand', condense: 'expand', keep: 'shrink' }

export function contentFillValue(mode: ContentFillMode): ServiceContentFill {
  return SERVICE_VALUE[mode]
}

export function isContentFillMode(value: unknown): value is ContentFillMode {
  return CONTENT_FILL_MODES.some((mode) => mode.value === value)
}

export function suggestedContentFill(status: SufficiencyStatus | null | undefined): ContentFillMode | null {
  if (status === 'short') return 'expand'
  if (status === 'excess') return 'condense'
  return null
}
