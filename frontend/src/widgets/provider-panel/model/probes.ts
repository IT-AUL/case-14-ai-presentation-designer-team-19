import { PROBE_STATUS_LABEL } from '@/entities/provider-session'
import type { CapabilityProbe } from '@/entities/provider-session'

const PROBE_DETAIL_LABEL: Record<string, string> = {
  not_declared: 'Не заявлено в подключении',
  not_probeable: 'Автоматически не проверяется',
  connection_error: 'Сервис не ответил',
  timeout: 'Сервис не ответил вовремя',
  unauthorized: 'Токен не подошёл',
}

export function probeDetail(probe: CapabilityProbe): string {
  if (!probe.detail) return PROBE_STATUS_LABEL[probe.status]
  return PROBE_DETAIL_LABEL[probe.detail] ?? probe.detail
}
