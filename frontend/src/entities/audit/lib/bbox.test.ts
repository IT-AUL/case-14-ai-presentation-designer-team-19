import { variantFixtures as fixtures } from '@/shared/api/mocks'
import { boxStyle, clampBox, MIN_BOX_SIZE } from './bbox'

describe('clampBox', () => {
  it('keeps boxes inside the slide untouched', () => {
    expect(clampBox({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 })).toEqual({ x: 0.1, y: 0.2, w: 0.3, h: 0.4, clipped: false })
  })

  it('cuts boxes that cross the slide edge', () => {
    const box = clampBox({ x: 0.7, y: 0.9, w: 0.5, h: 0.2 })
    expect(box?.clipped).toBe(true)
    expect(box?.x).toBeCloseTo(0.7)
    expect(box?.w).toBeCloseTo(0.3)
    expect(box?.y).toBeCloseTo(0.9)
    expect(box?.h).toBeCloseTo(0.1)
  })

  it('pins boxes lying fully below the slide to the bottom edge', () => {
    const box = clampBox({ x: 0.21, y: 1.087, w: 0.2, h: 0.067 })
    expect(box).toMatchObject({ y: 1 - MIN_BOX_SIZE, h: MIN_BOX_SIZE, clipped: true })
  })

  it('pins boxes lying fully above or left of the slide to the start edge', () => {
    expect(clampBox({ x: -0.5, y: -0.3, w: 0.2, h: 0.1 })).toMatchObject({ x: 0, y: 0, w: MIN_BOX_SIZE, h: MIN_BOX_SIZE, clipped: true })
  })

  it('rejects missing or malformed boxes', () => {
    expect(clampBox(null)).toBeNull()
    expect(clampBox(undefined)).toBeNull()
    expect(clampBox({ x: Number.NaN, y: 0, w: 0.1, h: 0.1 })).toBeNull()
    expect(clampBox({ x: 0, y: 0, w: -0.1, h: 0.1 })).toBeNull()
  })

  it('keeps every real backend box within the slide', () => {
    const issues = Object.values(fixtures).flatMap((variant) => variant.issues)
    const boxes = issues.map((issue) => clampBox(issue.bbox))
    expect(boxes.some((box) => box?.clipped)).toBe(true)
    for (const box of boxes) {
      if (!box) continue
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.y).toBeGreaterThanOrEqual(0)
      expect(box.x + box.w).toBeLessThanOrEqual(1 + 1e-9)
      expect(box.y + box.h).toBeLessThanOrEqual(1 + 1e-9)
    }
  })

  it('renders boxes as percentages', () => {
    expect(boxStyle({ x: 0.1, y: 0.25, w: 0.5, h: 0.125 })).toEqual({ left: '10.000%', top: '25.000%', width: '50.000%', height: '12.500%' })
  })
})
