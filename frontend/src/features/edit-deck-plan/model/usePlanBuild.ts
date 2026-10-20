import { useCallback, useEffect, useRef, useState } from 'react'
import { EMPTY_PROGRESS, type PlanProgress, type PlanRequestOptions } from '@/entities/generation'

export type PlanBuildPhase = 'content' | 'plan'

export interface PlanBuild {
  progress: PlanProgress
  phase: PlanBuildPhase | null
  startedAt: number | null
  begin: () => PlanRequestOptions
  enterPhase: (phase: PlanBuildPhase) => void
  cancel: () => void
  finish: () => void
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException ? error.name === 'AbortError' : error instanceof Error && error.name === 'AbortError'
}

export function usePlanBuild(): PlanBuild {
  const [progress, setProgress] = useState<PlanProgress>(EMPTY_PROGRESS)
  const [phase, setPhase] = useState<PlanBuildPhase | null>(null)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const controller = useRef<AbortController | null>(null)

  const begin = useCallback((): PlanRequestOptions => {
    controller.current?.abort()
    const next = new AbortController()
    controller.current = next
    setProgress(EMPTY_PROGRESS)
    setPhase('content')
    setStartedAt(Date.now())
    return {
      signal: next.signal,
      onProgress: (value) => {
        if (controller.current === next) setProgress(value)
      },
    }
  }, [])

  const finish = useCallback(() => {
    controller.current = null
    setPhase(null)
    setStartedAt(null)
  }, [])

  const cancel = useCallback(() => {
    controller.current?.abort(new DOMException('Построение плана отменено', 'AbortError'))
    finish()
  }, [finish])

  useEffect(() => () => controller.current?.abort(), [])

  return { progress, phase, startedAt, begin, enterPhase: setPhase, cancel, finish }
}
