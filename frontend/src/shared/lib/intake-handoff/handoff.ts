export interface IntakeHandoff {
  idea: string
  files: File[]
}

const KEY = 'intakeHandoff'

export function intakeHandoffState(handoff: IntakeHandoff): Record<string, IntakeHandoff> {
  return { [KEY]: handoff }
}

export function readIntakeHandoff(state: unknown): IntakeHandoff | null {
  if (!state || typeof state !== 'object') return null
  const value = (state as Record<string, unknown>)[KEY]
  if (!value || typeof value !== 'object') return null
  const { idea, files } = value as Partial<IntakeHandoff>
  const safeIdea = typeof idea === 'string' ? idea : ''
  const safeFiles = Array.isArray(files) ? files.filter((file): file is File => file instanceof File) : []
  if (!safeIdea.trim() && safeFiles.length === 0) return null
  return { idea: safeIdea, files: safeFiles }
}
