import { GENERATION_BUDGET_SECONDS } from '@/shared/config'

export interface GenerationTiming {
  startedAt: number | null
  finishedAt: number | null
}

export interface BudgetStatus {
  budgetSeconds: number
  fraction: number
  remainingSeconds: number
  exceeded: boolean
}

export function elapsedSeconds({ startedAt, finishedAt }: GenerationTiming, now: number): number {
  if (startedAt === null) return 0
  return Math.max(0, ((finishedAt ?? now) - startedAt) / 1000)
}

export function budgetStatus(elapsed: number, budgetSeconds = GENERATION_BUDGET_SECONDS): BudgetStatus {
  return {
    budgetSeconds,
    fraction: Math.min(1, elapsed / budgetSeconds),
    remainingSeconds: Math.max(0, budgetSeconds - elapsed),
    exceeded: elapsed > budgetSeconds,
  }
}
