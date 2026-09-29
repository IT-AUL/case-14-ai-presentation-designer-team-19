import type { SlidePurpose } from '../model/types'

export const SLIDE_PURPOSE_LABEL: Record<SlidePurpose, string> = {
  title: 'Титул',
  agenda: 'Повестка',
  section_divider: 'Раздел',
  overview: 'Обзор',
  problem: 'Проблема',
  solution: 'Решение',
  benefits: 'Выгоды',
  data: 'Данные',
  comparison: 'Сравнение',
  timeline: 'Таймлайн',
  process: 'Процесс',
  quote: 'Цитата',
  team: 'Команда',
  cta: 'Призыв к действию',
  qa: 'Вопросы и ответы',
  thank_you: 'Финал',
  custom: 'Свой слайд',
}

function isSlidePurpose(value: string): value is SlidePurpose {
  return value in SLIDE_PURPOSE_LABEL
}

export function slidePurposeLabel(purpose: string | null | undefined): string | null {
  if (!purpose) return null
  return isSlidePurpose(purpose) ? SLIDE_PURPOSE_LABEL[purpose] : purpose
}
