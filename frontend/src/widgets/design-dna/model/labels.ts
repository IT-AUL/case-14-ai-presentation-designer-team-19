import type { AnchorKind, DnaGroup, UnsupportedStrategy } from '@/entities/template'
import { formatNumber, formatPercent, pluralize } from '@/shared/lib/format'
import type { Tone } from '@/shared/ui'

export const FORMS = {
  slide: ['слайд', 'слайда', 'слайдов'],
  master: ['мастер', 'мастера', 'мастеров'],
  layout: ['макет', 'макета', 'макетов'],
  slot: ['слот', 'слота', 'слотов'],
  color: ['цвет', 'цвета', 'цветов'],
  fragment: ['фрагмент', 'фрагмента', 'фрагментов'],
  size: ['кегль', 'кегля', 'кеглей'],
  anchor: ['якорь', 'якоря', 'якорей'],
  role: ['роль', 'роли', 'ролей'],
  line: ['линия', 'линии', 'линий'],
  margin: ['поле', 'поля', 'полей'],
  font: ['шрифт', 'шрифта', 'шрифтов'],
} as const satisfies Record<string, readonly [string, string, string]>

export const OF_FORMS = {
  slide: ['слайда', 'слайдов'],
  layout: ['макета', 'макетов'],
  fragment: ['фрагмента', 'фрагментов'],
} as const satisfies Record<string, readonly [string, string]>

export function ofCountLabel(count: number, forms: readonly [string, string]): string {
  const singular = count % 10 === 1 && count % 100 !== 11
  return `${formatNumber(count)} ${singular ? forms[0] : forms[1]}`
}

export function countLabel(count: number, forms: readonly [string, string, string]): string {
  return `${formatNumber(count)} ${pluralize(count, forms)}`
}

export const LOW_CONFIDENCE = 0.75

export const GROUP_LABEL: Record<DnaGroup, string> = {
  palette: 'Палитра',
  fonts: 'Шрифты',
  sizes: 'Шкала кеглей',
  spacing: 'Сетка и поля',
  anchors: 'Якоря',
  layouts: 'Макеты',
  roles: 'Роли слайдов',
}

export const ANCHOR_LABEL: Record<AnchorKind, string> = {
  logo: 'Лого',
  footer: 'Колонтитул',
  page_number: '№',
  date: 'Дата',
  watermark: 'Водяной знак',
}

export const STRATEGY_LABEL: Record<UnsupportedStrategy, { label: string; tone: Tone }> = {
  preserve: { label: 'сохраняется как есть', tone: 'neutral' },
  immutable_decoration: { label: 'сохраняется как декор', tone: 'neutral' },
  raster_fallback: { label: 'растрируется, в отчёте', tone: 'warn' },
  skip: { label: 'пропускается, в отчёте', tone: 'warn' },
}

const ROLE_LABEL: Record<string, string> = {
  title: 'Титул',
  cover: 'Титул',
  section: 'Раздел',
  section_header: 'Раздел',
  divider: 'Раздел',
  agenda: 'Содержание',
  toc: 'Содержание',
  content: 'Контент',
  body: 'Контент',
  text: 'Текст',
  comparison: 'Сравнение',
  chart: 'График',
  table: 'Таблица',
  image: 'Изображение',
  picture: 'Изображение',
  quote: 'Цитата',
  contact: 'Визитка',
  closing: 'Финал',
  ending: 'Финал',
  thanks: 'Финал',
  blank: 'Пустой',
  subtitle: 'Подзаголовок',
  footer: 'Колонтитул',
  caption: 'Подпись',
  footnote: 'Сноска',
}

const PLACEHOLDER_LABEL: Record<string, string> = {
  title: 'заголовок',
  ctrTitle: 'заголовок',
  subTitle: 'подзаголовок',
  body: 'текст',
  obj: 'объект',
  pic: 'картинка',
  chart: 'график',
  tbl: 'таблица',
  dt: 'дата',
  ftr: 'колонтитул',
  sldNum: '№',
}

const TEXT_ROLE_LABEL: Record<string, string> = {
  title: 'заголовок',
  subtitle: 'подзаголовок',
  body: 'текст',
  caption: 'подпись',
  footer: 'колонтитул',
  label: 'метка',
  display: 'акцент',
}

const LAYOUT_TYPE_LABEL: Record<string, string> = {
  title: 'титул',
  title_only: 'только заголовок',
  section: 'раздел',
  content: 'контент',
  two_content: 'две колонки',
  comparison: 'сравнение',
  picture: 'картинка',
  closing: 'финал',
  blank: 'пустой',
  custom: 'свой',
}

export function textRoleLabel(role: string): string {
  return TEXT_ROLE_LABEL[role] ?? TEXT_ROLE_LABEL[role.toLowerCase()] ?? role
}

export function layoutTypeLabel(type: string): string {
  return LAYOUT_TYPE_LABEL[type] ?? LAYOUT_TYPE_LABEL[type.toLowerCase()] ?? type.replace(/_/g, ' ')
}

export function roleLabel(role: string): string {
  return ROLE_LABEL[role] ?? ROLE_LABEL[role.toLowerCase()] ?? role
}

export function placeholderLabel(type: string): string {
  return PLACEHOLDER_LABEL[type] ?? type
}

export function isTitlePlaceholder(type: string): boolean {
  return type === 'title' || type === 'ctrTitle'
}

export function formatFraction(value: number): string {
  const fixed = value.toFixed(3)
  return fixed.startsWith('0.') ? fixed.slice(1) : fixed.startsWith('-0.') ? `-${fixed.slice(2)}` : fixed
}

export function formatPt(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

export function partName(part: string): string {
  return part.split('/').pop()?.replace(/\.xml$/, '') ?? part
}

export function formatShare(share: number): string {
  if (share > 0 && share < 0.005) return '<1%'
  return formatPercent(share)
}
