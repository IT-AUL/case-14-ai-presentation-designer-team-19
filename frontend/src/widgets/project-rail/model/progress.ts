import { routes, type ProjectStep } from '@/shared/config'

export interface ProjectProgress {
  projectId: string
  runId?: string
  runPending?: boolean
  hasTemplate: boolean
  hasContent: boolean
  targetSlides: number
  planFirst?: boolean
  hasPlanDraft?: boolean
  generated: boolean
  repaired: boolean
  exported: boolean
  reaudited: boolean
}

export type StepState = 'current' | 'done' | 'available' | 'locked'

export interface RailStep {
  id: ProjectStep
  label: string
  meta: string
  state: StepState
  href?: string
}

export interface ProofLink {
  label: string
  done: boolean
}

const STEP_ORDER: ProjectStep[] = ['template', 'brief', 'plan', 'run', 'variants', 'audit', 'export']

function stepHref(step: ProjectStep, progress: ProjectProgress): string | undefined {
  const { projectId, runId } = progress
  if (step === 'template') return routes.template(projectId)
  if (step === 'brief') return progress.hasTemplate ? routes.brief(projectId) : undefined
  if (step === 'plan' && progress.hasPlanDraft && (!runId || !progress.generated)) return routes.planDraft(projectId)
  if (!runId) return undefined
  if (step === 'plan') return routes.plan(projectId, runId)
  if (step === 'run') return routes.run(projectId, runId)
  if (progress.runPending) return undefined
  if (step === 'variants') return routes.variants(projectId, runId)
  if (step === 'audit') return routes.audit(projectId, runId)
  return routes.export(projectId, runId)
}

function stepMeta(step: ProjectStep, progress: ProjectProgress): string {
  switch (step) {
    case 'template':
      return progress.hasTemplate ? 'разобран' : 'загрузите PPTX'
    case 'brief':
      return progress.hasContent ? `контент · ${progress.targetSlides} слайдов` : `${progress.targetSlides} слайдов`
    case 'plan':
      return progress.planFirst ? 'до вёрстки, можно править' : 'структура колоды'
    case 'run':
      return 'три раскладки'
    case 'variants':
      return 'три стратегии'
    case 'audit':
      return progress.repaired ? 'есть новая ревизия' : 'D + N проверки'
    case 'export':
      return 'PPTX · PDF · паспорт'
  }
}

function isDone(step: ProjectStep, progress: ProjectProgress): boolean {
  switch (step) {
    case 'template':
      return progress.hasTemplate
    case 'brief':
      return progress.hasContent
    case 'plan':
    case 'run':
    case 'variants':
      return progress.generated
    case 'audit':
      return progress.repaired
    case 'export':
      return progress.exported
  }
}

const STEP_LABEL: Record<ProjectStep, string> = {
  template: 'Шаблон',
  brief: 'Бриф и контент',
  plan: 'План',
  run: 'Генерация',
  variants: 'Три варианта',
  audit: 'Аудит и правки',
  export: 'Экспорт',
}

export function buildRailSteps(progress: ProjectProgress, current: ProjectStep | undefined): RailStep[] {
  return STEP_ORDER.map((id) => {
    const href = stepHref(id, progress)
    const state: StepState = id === current ? 'current' : !href ? 'locked' : isDone(id, progress) ? 'done' : 'available'
    return { id, label: STEP_LABEL[id], meta: stepMeta(id, progress), state, href }
  })
}

export function buildProofChain(progress: ProjectProgress): ProofLink[] {
  return [
    { label: 'Неизвестный PPTX', done: progress.hasTemplate },
    { label: 'Извлечённые правила', done: progress.hasTemplate },
    { label: 'План', done: progress.generated },
    { label: 'Три стратегии', done: progress.generated },
    { label: 'Подсвеченные нарушения', done: progress.generated },
    { label: 'Выбранные исправления', done: progress.repaired },
    { label: 'Нативный PPTX', done: progress.exported },
    { label: 'Повторный аудит: 0 блокеров', done: progress.reaudited },
  ]
}

export function stepFromPath(pathname: string): ProjectStep | undefined {
  const segments = pathname.split('/').filter(Boolean)
  const [, , section, , sub] = segments
  if (section === 'template' || section === 'brief' || section === 'plan') return section
  if (section !== 'runs') return undefined
  if (sub === 'plan' || sub === 'variants' || sub === 'audit' || sub === 'export') return sub
  return 'run'
}
