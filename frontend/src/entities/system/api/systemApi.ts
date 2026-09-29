import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/shared/api'
import { isCapabilityAvailable } from '../lib/capabilities'
import type { Capabilities, HealthStatus, SkillManifest, VersionInfo } from '../model/types'

const STATIC_STALE_TIME_MS = 5 * 60_000
const HEALTH_INTERVAL_MS = 30_000

export const systemKeys = {
  all: ['system'] as const,
  capabilities: () => ['system', 'capabilities'] as const,
  version: () => ['system', 'version'] as const,
  skillManifest: () => ['system', 'skill-manifest'] as const,
  health: () => ['system', 'health'] as const,
}

export function fetchCapabilities(): Promise<Capabilities> {
  return unwrap(api.GET('/api/v1/capabilities'))
}

export async function supportsFeature(paths: readonly string[]): Promise<boolean> {
  const capabilities = await fetchCapabilities()
  return paths.some((path) => isCapabilityAvailable(capabilities, path))
}

export function useCapabilities() {
  return useQuery({
    queryKey: systemKeys.capabilities(),
    queryFn: fetchCapabilities,
    staleTime: STATIC_STALE_TIME_MS,
  })
}

export function useVersion() {
  return useQuery({
    queryKey: systemKeys.version(),
    queryFn: (): Promise<VersionInfo> => unwrap(api.GET('/api/v1/version')),
    staleTime: STATIC_STALE_TIME_MS,
  })
}

export function useSkillManifest() {
  return useQuery({
    queryKey: systemKeys.skillManifest(),
    queryFn: (): Promise<SkillManifest> => unwrap(api.GET('/api/v1/skill/manifest')),
    staleTime: STATIC_STALE_TIME_MS,
  })
}

export function useHealth() {
  return useQuery({
    queryKey: systemKeys.health(),
    queryFn: (): Promise<HealthStatus> => unwrap(api.GET('/api/v1/health/ready')),
    refetchInterval: HEALTH_INTERVAL_MS,
    retry: false,
  })
}

export function useCapabilityFlag(paths: readonly string[]): boolean {
  const { data } = useCapabilities()
  return paths.some((path) => isCapabilityAvailable(data, path))
}
