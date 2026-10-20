export type AssistantStepNumber = 1 | 2 | 3 | 4 | 5 | 6 | 7

export type AssistantStepState = 'done' | 'current' | 'skipped' | 'todo'

export interface AssistantStepView {
  number: AssistantStepNumber
  label: string
  state: AssistantStepState
  href?: string
  hint?: string
}

export interface AssistantStepSnapshot {
  completed: readonly number[]
  skipped: readonly number[]
  current?: number | null
  hrefs: Partial<Record<AssistantStepNumber, string>>
}

export const ASSISTANT_STEPS: readonly { number: AssistantStepNumber; label: string }[] = [
  { number: 1, label: 'Материалы' },
  { number: 2, label: 'Что я понял' },
  { number: 3, label: 'Вопросы' },
  { number: 4, label: 'План' },
  { number: 5, label: 'Стиль' },
  { number: 6, label: 'Сборка' },
  { number: 7, label: 'Доводка' },
]

const SKIPPED_HINT = 'Понял сам — уточнять не пришлось'

const PATH_STEPS: readonly [RegExp, AssistantStepNumber][] = [
  [/\/runs\/[^/]+\/(polish|audit|export)(\/|$)/, 7],
  [/\/runs\/[^/]+\/variants(\/|$)/, 6],
  [/\/runs\/[^/]+\/plan(\/|$)/, 4],
  [/\/runs\/[^/]+\/?$/, 6],
  [/\/style\/?$/, 5],
  [/\/plan\/?$/, 4],
  [/\/questions\/?$/, 3],
  [/\/understanding\/?$/, 2],
  [/\/(start|template|brief)\/?$/, 1],
]

export function assistantStepFromPath(pathname: string): AssistantStepNumber | undefined {
  return PATH_STEPS.find(([pattern]) => pattern.test(pathname))?.[1]
}

export function buildAssistantSteps(snapshot: AssistantStepSnapshot, viewing?: AssistantStepNumber): AssistantStepView[] {
  const current = viewing ?? snapshot.current ?? undefined
  return ASSISTANT_STEPS.map(({ number, label }) => {
    const skipped = snapshot.skipped.includes(number)
    const state: AssistantStepState =
      number === current ? 'current' : skipped ? 'skipped' : snapshot.completed.includes(number) ? 'done' : 'todo'
    const href = snapshot.hrefs[number]
    return { number, label, state, ...(href ? { href } : {}), ...(skipped ? { hint: SKIPPED_HINT } : {}) }
  })
}
