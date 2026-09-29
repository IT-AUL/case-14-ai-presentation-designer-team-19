import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { startGeneration, whenGenerationAccepted, type GenerationAccepted } from '@/entities/generation'
import { latestRunId } from '@/entities/project'
import { createBriefForm } from './form'
import { useSubmitBrief } from './mutations'

vi.mock('@/entities/generation', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  startGeneration: vi.fn(),
  whenGenerationAccepted: vi.fn(),
}))

const PROJECT = 'prj_submit'

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useSubmitBrief', () => {
  beforeEach(() => window.localStorage.clear())

  it('remembers the run once the service accepts it, even if the generation screen is gone', async () => {
    let accept: (value: GenerationAccepted) => void = () => undefined
    const accepted = new Promise<GenerationAccepted>((resolve) => {
      accept = resolve
    })
    vi.mocked(startGeneration).mockReturnValue('local-test-1')
    vi.mocked(whenGenerationAccepted).mockReturnValue(accepted)

    const { result, unmount } = renderHook(() => useSubmitBrief(PROJECT), { wrapper })
    const form = { ...createBriefForm({ contentPackId: 'cp_1' }), purpose: 'product' as const }
    await act(() => result.current.mutateAsync({ form, files: [], templateId: 'tpl_1', providerSessionId: null }))
    unmount()

    expect(latestRunId(PROJECT)).toBeUndefined()
    accept({ generation_id: 'run_real', job_id: 'job_1', variant_ids: [] })
    await waitFor(() => expect(latestRunId(PROJECT)).toBe('run_real'))
  })
})
