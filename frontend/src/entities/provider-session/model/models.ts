import type { ModelRole, ProviderCapabilities } from './types'

export type ModelLicense = 'Apache-2.0' | 'MIT'

export interface SuggestedModel {
  id: string
  role: ModelRole
  parametersB: number
  license: ModelLicense
  url: string
}

export const ALLOWED_MODEL_LICENSES: readonly ModelLicense[] = ['Apache-2.0', 'MIT']

export const MODEL_PARAMETER_LIMIT_B: Record<ModelRole, number> = {
  text: 35,
  vision: 35,
  embedding: 35,
  image: 20,
}

export const SUGGESTED_MODELS: Record<ModelRole, SuggestedModel> = {
  text: {
    id: 'Qwen/Qwen3-32B',
    role: 'text',
    parametersB: 32.8,
    license: 'Apache-2.0',
    url: 'https://huggingface.co/Qwen/Qwen3-32B',
  },
  vision: {
    id: 'Qwen/Qwen2.5-VL-32B-Instruct',
    role: 'vision',
    parametersB: 33.5,
    license: 'Apache-2.0',
    url: 'https://huggingface.co/Qwen/Qwen2.5-VL-32B-Instruct',
  },
  embedding: {
    id: 'BAAI/bge-m3',
    role: 'embedding',
    parametersB: 0.57,
    license: 'MIT',
    url: 'https://huggingface.co/BAAI/bge-m3',
  },
  image: {
    id: 'black-forest-labs/FLUX.1-schnell',
    role: 'image',
    parametersB: 12,
    license: 'Apache-2.0',
    url: 'https://huggingface.co/black-forest-labs/FLUX.1-schnell',
  },
}

export const DEFAULT_PROVIDER_CAPABILITIES: ProviderCapabilities = {
  structured_output: true,
  tool_calls: false,
  image_input: true,
  embeddings: false,
}

export const DEFAULT_SESSION_LIMITS = {
  timeout_seconds: 90,
  max_concurrency: 4,
  ttl_seconds: 14_400,
} as const
