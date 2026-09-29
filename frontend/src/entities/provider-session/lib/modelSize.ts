import { MODEL_PARAMETER_LIMIT_B } from '../model/models'
import type { ModelRole } from '../model/types'

const SIZE_TOKEN = /(?:^|[^a-z0-9.])(\d+(?:\.\d+)?)\s*([bm])(?![a-z0-9])/gi

export function modelParameterCountB(modelId: string): number | null {
  const sizes = [...modelId.matchAll(SIZE_TOKEN)].map(([, value, unit]) => Number(value) / (unit?.toLowerCase() === 'm' ? 1000 : 1))
  return sizes.length > 0 ? Math.max(...sizes) : null
}

export function exceedsParameterLimit(modelId: string, role: ModelRole): boolean {
  const size = modelParameterCountB(modelId)
  return size !== null && size > MODEL_PARAMETER_LIMIT_B[role]
}
