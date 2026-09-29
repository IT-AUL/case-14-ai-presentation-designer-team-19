import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { clearOpenCounts, readOpenCounts, reportOpenCounts, useOpenCounts } from './openCounts'

afterEach(() => clearOpenCounts())

describe('open counts store', () => {
  it('shares the latest open blocker and error counts per project', () => {
    const { result } = renderHook(() => useOpenCounts('p1', 'run-1'))
    expect(result.current).toBeNull()
    act(() => reportOpenCounts('p1', { blocker: 1, error: 54 }, 'run-1'))
    expect(result.current).toEqual({ runId: 'run-1', blocker: 1, error: 54 })
    act(() => reportOpenCounts('p1', { blocker: 0, error: 52 }, 'run-1'))
    expect(result.current).toMatchObject({ blocker: 0, error: 52 })
  })

  it('ignores counts reported for another run of the project', () => {
    reportOpenCounts('p1', { blocker: 0, error: 3 }, 'run-old')
    expect(readOpenCounts('p1', 'run-new')).toBeNull()
    expect(readOpenCounts('p1', 'run-old')).toEqual({ blocker: 0, error: 3 })
    expect(readOpenCounts('p2')).toBeNull()
  })

  it('keeps the same snapshot when nothing changed', () => {
    const { result } = renderHook(() => useOpenCounts('p1'))
    act(() => reportOpenCounts('p1', { blocker: 0, error: 2 }))
    const first = result.current
    act(() => reportOpenCounts('p1', { blocker: 0, error: 2 }))
    expect(result.current).toBe(first)
  })
})
