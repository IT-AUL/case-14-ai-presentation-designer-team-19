import type { AuditIssue } from '../model/types'

export interface OverlayBox {
  x: number
  y: number
  w: number
  h: number
  clipped: boolean
}

export const MIN_BOX_SIZE = 0.012

const EPSILON = 1e-6

const clamp01 = (value: number) => Math.min(1, Math.max(0, value))

function clampAxis(start: number, size: number): [number, number] {
  if (start >= 0 && start + size <= 1 && size >= MIN_BOX_SIZE) return [start, size]
  const from = clamp01(start)
  const to = clamp01(start + size)
  if (to - from >= MIN_BOX_SIZE) return [from, to - from]
  if (from >= 1 - MIN_BOX_SIZE) return [1 - MIN_BOX_SIZE, MIN_BOX_SIZE]
  return [from, MIN_BOX_SIZE]
}

export function clampBox(bbox: AuditIssue['bbox']): OverlayBox | null {
  if (!bbox) return null
  const { x, y, w, h } = bbox
  if (typeof x !== 'number' || typeof y !== 'number' || typeof w !== 'number' || typeof h !== 'number') return null
  if (![x, y, w, h].every(Number.isFinite) || w < 0 || h < 0) return null
  const [cx, cw] = clampAxis(x, w)
  const [cy, ch] = clampAxis(y, h)
  const moved = (a: number, b: number) => Math.abs(a - b) > EPSILON
  return { x: cx, y: cy, w: cw, h: ch, clipped: moved(cx, x) || moved(cy, y) || moved(cw, w) || moved(ch, h) }
}

export function boxStyle(box: Pick<OverlayBox, 'x' | 'y' | 'w' | 'h'>): { left: string; top: string; width: string; height: string } {
  const percent = (value: number) => `${(value * 100).toFixed(3)}%`
  return { left: percent(box.x), top: percent(box.y), width: percent(box.w), height: percent(box.h) }
}
