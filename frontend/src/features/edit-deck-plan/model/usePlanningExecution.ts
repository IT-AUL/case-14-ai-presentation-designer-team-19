import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, isCapabilityAvailable, useCapabilities } from '@/entities/system'

export function resolvePlanningExecution(modelAvailable: boolean, providerSessionId?: string | null) {
  return {
    use_llm: modelAvailable || Boolean(providerSessionId),
    ...(providerSessionId ? { provider_session_id: providerSessionId } : {}),
  }
}

export function usePlanningExecution() {
  const providerSession = useActiveProviderSession()
  const capabilities = useCapabilities()
  const available = FEATURE_PATHS.modelAuto.some((path) => isCapabilityAvailable(capabilities.data, path))
  return {
    ready: Boolean(providerSession) || !capabilities.isPending,
    ...resolvePlanningExecution(available, providerSession?.id),
  }
}
