import { readJson, writeJson } from '@/shared/lib/storage'
import type { Project } from '../model/types'
import { resetEvidence } from './evidence'

const STORAGE_KEY = 'deckdna.runs.v1'

type RunHistory = Record<string, string[]>

export function readRunIds(projectId: string): string[] {
  return readJson<RunHistory>(STORAGE_KEY, {})[projectId] ?? []
}

export function latestRunId(projectId: string): string | undefined {
  return readRunIds(projectId).at(-1)
}

export function projectRunId(project: Pick<Project, 'id' | 'latest_run_id'> | null | undefined, projectId: string): string | undefined {
  return project?.latest_run_id?.trim() || latestRunId(project?.id ?? projectId)
}

export function rememberRun(projectId: string, runId: string): void {
  const history = readJson<RunHistory>(STORAGE_KEY, {})
  const known = history[projectId] ?? []
  if (known.at(-1) === runId) return
  writeJson(STORAGE_KEY, { ...history, [projectId]: [...known.filter((id) => id !== runId), runId] })
  if (!known.includes(runId)) resetEvidence(projectId)
}
