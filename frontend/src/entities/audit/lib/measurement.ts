import type { AuditIssue } from '../model/types'
import { formatMeasurement } from './issues'

interface RuleUnit {
  quantity: string
  measured: (value: number) => string
  threshold: (value: number) => string
}

const plain = (value: number) => formatMeasurement(value)
const precise = (value: number) => (Number.isInteger(value) ? String(value) : String(Math.round(value * 1000) / 1000))
const percent = (value: number) => `${formatMeasurement(Math.round(value * 1000) / 10)}%`
const pt = (value: number) => `${formatMeasurement(value)} pt`
const atMost = (render: (value: number) => string) => (value: number) => `≤ ${render(value)}`
const atLeast = (render: (value: number) => string) => (value: number) => `≥ ${render(value)}`

const RULE_UNITS: Record<string, RuleUnit> = {
  'accessibility.contrast': { quantity: 'Контраст', measured: (value) => `${plain(value)}:1`, threshold: (value) => `≥ ${plain(value)}:1` },
  'text.font_floor': { quantity: 'Кегль', measured: pt, threshold: atLeast(pt) },
  'text.overflow': { quantity: 'Текст занимает', measured: pt, threshold: atMost(pt) },
  'template.color_palette': { quantity: 'Отличие от палитры', measured: (value) => `ΔE ${plain(value)}`, threshold: (value) => `ΔE ≤ ${plain(value)}` },
  'template.font_scale': { quantity: 'Отклонение от шкалы кеглей', measured: percent, threshold: atMost(percent) },
  'image.aspect_ratio': { quantity: 'Пропорции картинки', measured: precise, threshold: precise },
  'layout.out_of_bounds': { quantity: 'Выход за край', measured: percent, threshold: atMost(percent) },
  'layout.edge_margin': { quantity: 'Заход в поля', measured: percent, threshold: atMost(percent) },
  'text.slide_clip': { quantity: 'Обрезано краем', measured: percent, threshold: atMost(percent) },
  'density.bullet_count': { quantity: 'Буллетов', measured: plain, threshold: atMost(plain) },
  'density.bullet_length': { quantity: 'Слов в буллете', measured: plain, threshold: atMost(plain) },
  'density.chart_series': { quantity: 'Серий', measured: plain, threshold: atMost(plain) },
}

export interface MeasurementView {
  quantity: string | null
  measured: string
  threshold: string
  hasValue: boolean
}

export function describeMeasurement(issue: Pick<AuditIssue, 'rule_code' | 'measured_value' | 'threshold'>): MeasurementView {
  const unit = RULE_UNITS[issue.rule_code]
  const render = (value: AuditIssue['threshold'], withUnit: ((value: number) => string) | undefined) =>
    typeof value === 'number' && withUnit ? withUnit(value) : formatMeasurement(value)
  return {
    quantity: unit?.quantity ?? null,
    measured: render(issue.measured_value, unit?.measured),
    threshold: render(issue.threshold, unit?.threshold),
    hasValue: issue.measured_value !== null && issue.measured_value !== undefined,
  }
}

export function measurementSentence(issue: Pick<AuditIssue, 'rule_code' | 'measured_value' | 'threshold'>): string | null {
  const view = describeMeasurement(issue)
  if (!view.hasValue) return null
  const head = view.quantity ? `${view.quantity} ${view.measured}` : view.measured
  return view.threshold === '—' ? head : `${head}, норма ${view.threshold}`
}

const ACTION_LABEL: Record<string, string> = {
  map_font: 'подобрать кегль и шрифт из шаблона',
  reflow: 'перераспределить текст',
  shorten_text: 'сократить текст',
  split_text: 'разбить текст',
  resize_shape: 'увеличить рамку',
  map_color: 'заменить цвет на контрастный из палитры',
  recolor: 'перекрасить в цвет палитры',
  palette_substitution: 'заменить на ближайший цвет палитры',
  recrop_image: 'перекадрировать картинку',
  contain_image: 'вписать картинку без искажений',
  rewrite_title: 'переписать заголовок как вывод',
  rewrite_text: 'сократить текст моделью',
  move_shape: 'сдвинуть фигуру',
  crop_image: 'кадрировать картинку',
}

export function actionLabel(action: string): string {
  return ACTION_LABEL[action] ?? action
}
