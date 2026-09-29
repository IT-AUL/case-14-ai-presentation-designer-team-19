import type { Schemas } from '@/shared/api'

export type ProviderSession = Schemas['ProviderSession']
export type ProviderSessionCreate = Schemas['ProviderSessionCreate']
export type ProviderSessionTestResult = Schemas['ProviderSessionTestResult']
export type CapabilityProbe = Schemas['CapabilityProbe']
export type ProbeStatus = CapabilityProbe['status']
export type ModelSpec = Schemas['ModelSpec']
export type ProviderCapabilities = Schemas['ProviderCapabilities']
export type ModelRole = keyof ModelSpec

export interface ActiveProviderSession {
  id: string
  label: string
  baseUrl: string
  models: ModelSpec
  capabilities: ProviderCapabilities
  createdAt: string
  expiresAt: string
  lastTest: ProviderSessionTestResult | null
}
