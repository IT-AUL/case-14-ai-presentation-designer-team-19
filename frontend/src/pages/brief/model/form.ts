export type PurposeKey = 'feature' | 'product' | 'project' | 'initiative' | 'other'
export type ContentMode = 'file' | 'text'
export type BriefLanguage = 'ru' | 'en'

export interface PurposeOption {
  value: PurposeKey
  label: string
  hint: string
}

export const PURPOSES: readonly PurposeOption[] = [
  { value: 'feature', label: 'Фича', hint: 'отдельная функция продукта' },
  { value: 'product', label: 'Продукт', hint: 'приложение или услуга' },
  { value: 'project', label: 'Проект', hint: 'работа с результатами и отчётом' },
  { value: 'initiative', label: 'Инициатива', hint: 'предлагаемая деятельность' },
  { value: 'other', label: 'Другое', hint: 'опишу сам' },
]

export const LANGUAGES: readonly { value: BriefLanguage; label: string }[] = [
  { value: 'ru', label: 'Русский' },
  { value: 'en', label: 'English' },
]

export const SLIDE_COUNT_MIN = 3
export const SLIDE_COUNT_MAX = 40
export const DEFAULT_SLIDE_COUNT = 12
export const DEFAULT_AUDIENCE = 'Руководство'

export interface ParsedContent {
  packId: string
  sourceKey: string
  label: string
  sizeBytes: number | null
}

export interface BriefForm {
  purpose: PurposeKey | null
  customPurpose: string
  audience: string
  tone: string
  language: BriefLanguage
  slideCount: number
  mandatorySections: string[]
  forbiddenClaims: string[]
  contentMode: ContentMode
  text: string
  useLlm: boolean
  parsed: ParsedContent | null
}

export interface BriefDefaults {
  language?: string | null
  targetSlideCount?: number | null
  contentPackId?: string | null
}

export const PROJECT_CONTENT_KEY = 'project'

export function clampSlideCount(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_SLIDE_COUNT
  return Math.min(SLIDE_COUNT_MAX, Math.max(SLIDE_COUNT_MIN, Math.round(value)))
}

export function createBriefForm(defaults: BriefDefaults = {}): BriefForm {
  return {
    purpose: null,
    customPurpose: '',
    audience: DEFAULT_AUDIENCE,
    tone: '',
    language: defaults.language === 'en' ? 'en' : 'ru',
    slideCount: clampSlideCount(defaults.targetSlideCount ?? DEFAULT_SLIDE_COUNT),
    mandatorySections: [],
    forbiddenClaims: [],
    contentMode: 'file',
    text: '',
    useLlm: false,
    parsed: defaults.contentPackId
      ? { packId: defaults.contentPackId, sourceKey: PROJECT_CONTENT_KEY, label: 'Контент проекта', sizeBytes: null }
      : null,
  }
}
