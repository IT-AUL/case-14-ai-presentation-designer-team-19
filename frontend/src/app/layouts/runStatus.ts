import type { OpenCounts } from '@/entities/audit'
import type { GenerationTracker } from '@/entities/generation'
import type { RunStatus } from '@/widgets/app-header'
import { isTrackingLost } from '@/widgets/generation-tracker'
import type { ProjectProgress } from '@/widgets/project-rail'
import type { ProjectStep } from '@/shared/config'
import { pluralize } from '@/shared/lib/format'

function readyLabel(count: number | undefined): string {
  return count ? `Готово · ${count} ${pluralize(count, ['вариант', 'варианта', 'вариантов'])}` : 'Готово'
}

const REVIEW_STEPS: readonly (ProjectStep | undefined)[] = ['audit', 'export']

export function runStatus(
  step: ProjectStep | undefined,
  progress: ProjectProgress,
  tracker: Pick<GenerationTracker, 'phase' | 'generation' | 'error'>,
  openCounts: OpenCounts | null = null,
): RunStatus | undefined {
  if (step === 'template') {
    return progress.hasTemplate ? { label: 'Шаблон разобран', tone: 'ok' } : { label: 'Нужен шаблон', tone: 'neutral' }
  }
  if (step === 'brief') {
    return progress.hasContent ? { label: 'Контент загружен', tone: 'ok' } : { label: 'Черновик брифа', tone: 'neutral' }
  }
  if (!step) return undefined
  const { phase, generation, error } = tracker
  if (isTrackingLost(error)) return { label: 'Статус запуска неизвестен', tone: 'neutral' }
  if (phase === 'failed') return { label: 'Ошибка генерации', tone: 'error' }
  if (phase === 'canceled') return { label: 'Генерация отменена', tone: 'neutral' }
  if (generation?.state === 'awaiting_user') return { label: 'Ждёт решения', tone: 'warn' }
  if (phase === 'submitting' || (phase === 'running' && (generation || !progress.generated))) {
    return { label: 'Идёт генерация', tone: 'info' }
  }
  const reviewing = REVIEW_STEPS.includes(step) && openCounts !== null
  const critical = openCounts ? openCounts.blocker + openCounts.error : 0
  if (reviewing && critical > 0) return { label: `Ждёт решения: ${critical} критич.`, tone: 'warn' }
  if (progress.reaudited) return { label: 'Повторный аудит пройден', tone: 'ok' }
  if (reviewing) return { label: step === 'export' ? 'Готово к экспорту' : 'Критичных проблем нет', tone: 'ok' }
  return { label: readyLabel(generation?.variants.length), tone: 'ok' }
}
