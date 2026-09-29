import { useSyncExternalStore } from 'react'

export interface OpenCounts {
  blocker: number
  error: number
}

interface Entry extends OpenCounts {
  runId: string | null
}

const entries = new Map<string, Entry>()
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function reportOpenCounts(projectId: string, next: OpenCounts, runId: string | null = null): void {
  const current = entries.get(projectId)
  if (current && current.runId === runId && current.blocker === next.blocker && current.error === next.error) return
  entries.set(projectId, { runId, blocker: next.blocker, error: next.error })
  emit()
}

function read(projectId: string, runId: string | null | undefined): Entry | null {
  const entry = entries.get(projectId)
  if (!entry) return null
  if (runId && entry.runId && entry.runId !== runId) return null
  return entry
}

export function readOpenCounts(projectId: string, runId?: string | null): OpenCounts | null {
  const entry = read(projectId, runId)
  return entry ? { blocker: entry.blocker, error: entry.error } : null
}

export function clearOpenCounts(projectId?: string): void {
  if (projectId === undefined) entries.clear()
  else if (!entries.delete(projectId)) return
  emit()
}

export function useOpenCounts(projectId: string, runId?: string | null): OpenCounts | null {
  return useSyncExternalStore(
    subscribe,
    () => read(projectId, runId),
    () => null,
  )
}
