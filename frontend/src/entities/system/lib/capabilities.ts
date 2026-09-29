import type { Capabilities } from '../model/types'

const AFFIRMATIVE = new Set(['available', 'enabled', 'ok', 'ready', 'supported', 'implemented', 'true', 'yes', 'on'])
const FLAG_KEYS = ['available', 'enabled', 'supported', 'implemented', 'status'] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isTruthyCapability(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value > 0
  if (typeof value === 'string') return AFFIRMATIVE.has(value.trim().toLowerCase())
  if (Array.isArray(value)) return value.length > 0
  if (isRecord(value)) {
    const flag = FLAG_KEYS.find((key) => key in value)
    return flag ? isTruthyCapability(value[flag]) : Object.keys(value).length > 0
  }
  return false
}

function includesName(list: readonly unknown[], name: string): boolean {
  return list.some((item) => {
    if (typeof item === 'string') return item.toLowerCase() === name.toLowerCase()
    if (isRecord(item)) return item.name === name || item.id === name || item.format === name
    return false
  })
}

export function isCapabilityAvailable(capabilities: Capabilities | null | undefined, name: string): boolean {
  if (!capabilities) return false
  const path = name.split('.').filter(Boolean)
  let current: unknown = capabilities
  for (const [position, segment] of path.entries()) {
    if (Array.isArray(current)) return position === path.length - 1 && includesName(current, segment)
    if (!isRecord(current) || !(segment in current)) return false
    current = current[segment]
  }
  return isTruthyCapability(current)
}
