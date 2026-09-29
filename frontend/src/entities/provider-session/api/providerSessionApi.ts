import { useMutation } from '@tanstack/react-query'
import { api, isApiError, unwrap } from '@/shared/api'
import { clearActiveProviderSession, recordProviderSessionTest, setActiveProviderSession } from '../model/activeSession'
import type { ProviderSession, ProviderSessionCreate, ProviderSessionTestResult } from '../model/types'

export function useCreateProviderSession() {
  return useMutation({
    mutationFn: (body: ProviderSessionCreate): Promise<ProviderSession> => unwrap(api.POST('/api/v1/provider-sessions', { body })),
    gcTime: 0,
    onSuccess: (session) => {
      setActiveProviderSession(session)
    },
  })
}

export function useTestProviderSession() {
  return useMutation({
    mutationFn: (sessionId: string): Promise<ProviderSessionTestResult> =>
      unwrap(api.POST('/api/v1/provider-sessions/{session_id}/test', { params: { path: { session_id: sessionId } } })),
    onSuccess: (result) => recordProviderSessionTest(result),
    onError: (error, sessionId) => {
      if (isApiError(error) && error.status === 404) clearActiveProviderSession(sessionId)
    },
  })
}

export function useDeleteProviderSession() {
  return useMutation({
    mutationFn: async (sessionId: string): Promise<void> => {
      try {
        await unwrap(api.DELETE('/api/v1/provider-sessions/{session_id}', { params: { path: { session_id: sessionId } } }))
      } catch (error) {
        if (!isApiError(error) || error.status !== 404) throw error
      }
    },
    onSuccess: (_, sessionId) => clearActiveProviderSession(sessionId),
  })
}
