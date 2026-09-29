import { act, renderHook } from '@testing-library/react'
import { markEvidence, useProjectEvidence } from './evidence'
import { latestRunId, projectRunId, readRunIds, rememberRun } from './runHistory'

describe('run history', () => {
  beforeEach(() => window.localStorage.clear())

  it('keeps the latest run last without duplicates', () => {
    rememberRun('prj_history', 'run_a')
    rememberRun('prj_history', 'run_b')
    rememberRun('prj_history', 'run_a')
    expect(readRunIds('prj_history')).toEqual(['run_b', 'run_a'])
    expect(latestRunId('prj_history')).toBe('run_a')
  })

  it('clears proof marks of the previous run when a new run is remembered', () => {
    const { result } = renderHook(() => useProjectEvidence('prj_evidence'))
    act(() => rememberRun('prj_evidence', 'run_1'))
    act(() => {
      markEvidence('prj_evidence', 'generated')
      markEvidence('prj_evidence', 'reaudited')
    })
    expect(result.current).toEqual({ generated: true, reaudited: true })

    act(() => rememberRun('prj_evidence', 'run_1'))
    expect(result.current).toEqual({ generated: true, reaudited: true })

    act(() => rememberRun('prj_evidence', 'run_2'))
    expect(result.current).toEqual({})
  })
})

describe('projectRunId', () => {
  it('prefers the latest run reported by the service', () => {
    rememberRun('p-srv', 'run_local')
    expect(projectRunId({ id: 'p-srv', latest_run_id: 'run_server' }, 'p-srv')).toBe('run_server')
    expect(projectRunId({ id: 'p-srv', latest_run_id: null }, 'p-srv')).toBe('run_local')
    expect(projectRunId(undefined, 'p-srv')).toBe('run_local')
  })
})
