import type { AuditRun, DisplayStatus } from '@/entities/audit'

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
