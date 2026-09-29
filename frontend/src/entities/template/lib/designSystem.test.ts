import { designDnaFixture, templateDetailFixture } from '@/shared/api/mocks'
import { buildDesignSystem } from './designSystem'

const system = buildDesignSystem(templateDetailFixture, designDnaFixture)

describe('buildDesignSystem', () => {
  it('reads the package inventory of a real template', () => {
    expect(system.counts).toMatchObject({ slides: 54, masters: 2, layouts: 39, themes: 3 })
    expect(system.aspectLabel).toBe('16:9')
    expect(system.sizeLabel).toBe('25,40 × 14,29 см')
  })

  it('detects the declared versus observed font conflict', () => {
    expect(system.declaredFonts).toEqual(['Arial'])
    expect(system.primaryFont).toBe('Play')
    expect(system.fontConflict).toBe(true)
  })

  it('orders theme slots and classifies observed colors', () => {
    expect(system.palette[0]).toEqual({ slot: 'dk1', hex: '#000000' })
    const accent = system.usedColors.find((color) => color.hex === '#0077FF')
    const grey = system.usedColors.find((color) => color.hex === '#8F8F8F')
    expect(accent?.match).toEqual({ kind: 'theme', slot: 'accent1' })
    expect(grey?.match.kind).toBe('off')
  })

  it('ranks layouts by slide usage', () => {
    expect(system.layoutUsage[0]).toMatchObject({ layout: '11', slides: 36 })
    expect(system.unusedLayouts).toBe(21)
  })
})
