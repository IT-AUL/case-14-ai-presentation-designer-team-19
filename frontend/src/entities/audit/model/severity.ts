import type { Tone } from '@/shared/ui'
import type { Severity } from './types'

interface SeverityMeta {
  label: string
  shortLabel: string
  tone: Tone
  color: string
  order: number
}

export const SEVERITY: Record<Severity, SeverityMeta> = {
  blocker: { label: 'Блокер', shortLabel: 'Блокер', tone: 'blocker', color: 'var(--blocker)', order: 0 },
  error: { label: 'Ошибка', shortLabel: 'Ошибка', tone: 'error', color: 'var(--error)', order: 1 },
  warning: { label: 'Предупреждение', shortLabel: 'Предупр.', tone: 'warn', color: 'var(--warn)', order: 2 },
  info: { label: 'Инфо', shortLabel: 'Инфо', tone: 'info', color: 'var(--info)', order: 3 },
}

export const SEVERITY_ORDER: readonly Severity[] = ['blocker', 'error', 'warning', 'info']

export const CRITICAL_SEVERITIES: readonly Severity[] = ['blocker', 'error']
