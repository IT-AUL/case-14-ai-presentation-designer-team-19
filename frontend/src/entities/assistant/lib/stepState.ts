import type { AssistantSession, AssistantStep } from '../model/types'

export function stepState(session: AssistantSession | null | undefined, step: AssistantStep): 'done' | 'current' | 'skipped' | 'todo' {
  if (!session) return 'todo'
  if (session.skipped_steps.includes(step)) return 'skipped'
  if (session.step === step) return 'current'
  if (session.completed_steps.includes(step)) return 'done'
  return 'todo'
}
