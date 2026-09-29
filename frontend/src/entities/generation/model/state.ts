import type { JobState } from './types'

export const POLL_INTERVAL_MS = 2000

export const TERMINAL_JOB_STATES: readonly JobState[] = ['completed', 'failed', 'canceled']

export function isTerminalState(state: JobState | null | undefined): boolean {
  return state !== null && state !== undefined && TERMINAL_JOB_STATES.includes(state)
}

export const JOB_STATE_LABEL: Record<JobState, string> = {
  queued: 'В очереди',
  running: 'Идёт',
  awaiting_user: 'Ждёт решения',
  completed: 'Готово',
  failed: 'Ошибка',
  canceled: 'Отменено',
}
