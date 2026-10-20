import { resolvePlanningExecution } from './usePlanningExecution'

describe('resolvePlanningExecution', () => {
  it('uses the server provider for a restored plan', () => {
    expect(resolvePlanningExecution(true)).toEqual({ use_llm: true })
  })

  it('preserves the selected provider session', () => {
    expect(resolvePlanningExecution(false, 'session-1')).toEqual({ use_llm: true, provider_session_id: 'session-1' })
  })

  it('uses deterministic planning when no provider is available', () => {
    expect(resolvePlanningExecution(false)).toEqual({ use_llm: false })
  })
})
