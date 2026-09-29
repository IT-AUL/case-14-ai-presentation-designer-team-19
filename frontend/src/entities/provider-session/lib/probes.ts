import type { CapabilityProbe, ProbeStatus } from '../model/types'

export const PROBE_STATUS_LABEL: Record<ProbeStatus, string> = {
  ok: 'Работает',
  fail: 'Не отвечает',
  skip: 'Не проверялось',
}

export const PROVIDER_CAPABILITY_LABEL: Record<string, string> = {
  structured_output: 'Структурированный ответ',
  tool_calls: 'Вызов инструментов',
  image_input: 'Картинки на входе',
  embeddings: 'Эмбеддинги',
}

export function capabilityLabel(capability: string): string {
  return PROVIDER_CAPABILITY_LABEL[capability] ?? capability
}

export function countProbes(results: readonly CapabilityProbe[]): Record<ProbeStatus, number> {
  const counts: Record<ProbeStatus, number> = { ok: 0, fail: 0, skip: 0 }
  for (const probe of results) counts[probe.status] += 1
  return counts
}

export function isSessionHealthy(results: readonly CapabilityProbe[]): boolean {
  const counts = countProbes(results)
  return counts.fail === 0 && counts.ok > 0
}
