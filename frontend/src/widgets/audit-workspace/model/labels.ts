import { ruleMeta, type AuditRun, type DisplayStatus } from '@/entities/audit'

export const STATUS_LABEL: Record<DisplayStatus, string> = {
  open: 'Открыта',
  selected: 'Выбрана',
  fixed: 'Исправлена',
  unresolved: 'Не удалось',
  dismissed: 'Отклонена',
}

type RunStatus = AuditRun['deterministic_status'] | AuditRun['contextual_status']

export const CHECK_STATUS_LABEL: Record<RunStatus, string> = {
  pending: 'в очереди',
  running: 'идёт',
  completed: 'готово',
  failed: 'ошибка',
  skipped: 'не запускались',
}

const EXTRA_RULE_NAMES: Record<string, string> = {
  'narrative.actiontitles': 'Заголовки-выводы на слайдах',
  'narrative.action_titles': 'Заголовки-выводы на слайдах',
  'chart.readability': 'Читаемость подписей графика',
  'deck.consistency': 'Единообразие оформления колоды',
  'deck.storyline': 'Связность повествования',
}

export function localizeRuleTitle(ruleCode: string): string {
  if (/[а-яё]/i.test(ruleCode)) return ruleCode
  const cleaned = ruleCode.replace(/^rule:/, '').trim()
  const meta = ruleMeta(cleaned)
  if (meta.name !== cleaned) return meta.name
  return EXTRA_RULE_NAMES[cleaned.toLowerCase()] ?? 'Замечание к оформлению колоды'
}
