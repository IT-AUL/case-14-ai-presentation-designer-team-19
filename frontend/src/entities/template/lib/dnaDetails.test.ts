import { designDnaFixture, templateDetailFixture } from '@/shared/api/mocks'
import type { DesignDna, TemplateDetail } from '../model/types'
import { analysisState } from './analysis'
import { buildDesignSystem } from './designSystem'
import {
  anchorViews,
  fontSizeScale,
  layoutBlueprints,
  masterTree,
  normalizeBox,
  slideRoles,
  spacingSummary,
  understandTemplate,
} from './dnaDetails'

const dna: DesignDna = designDnaFixture
const detail: TemplateDetail = templateDetailFixture
const WIDTH = dna.slide_size.width_emu
const HEIGHT = dna.slide_size.height_emu

function layoutPart(index: number) {
  return `ppt/slideLayouts/slideLayout${index}.xml`
}

const rich: DesignDna = {
  ...dna,
  declared: {
    ...dna.declared,
    masters: [
      { part: 'ppt/slideMasters/slideMaster1.xml', layout_parts: [layoutPart(1), layoutPart(11), layoutPart(19)], placeholders: [] },
      { part: 'ppt/slideMasters/slideMaster2.xml', layout_parts: [layoutPart(20)], placeholders: [] },
    ],
    layouts: dna.declared.layouts.map((layout) =>
      layout.part === layoutPart(11)
        ? {
            ...layout,
            name: '1_Контент',
            type: 'obj',
            placeholders: [
              { type: 'title', idx: 0, bbox: { x: WIDTH * 0.06, y: HEIGHT * 0.08, w: WIDTH * 0.8, h: HEIGHT * 0.12 } },
              { type: 'body', idx: 1, bbox: null },
            ],
          }
        : layout.part === layoutPart(19)
          ? { ...layout, name: 'Пустой', type: 'blank', placeholders: [{ type: 'title', idx: 0, bbox: { x: 0.1, y: 0.1, w: 0.5, h: 0.2 } }] }
          : layout,
    ),
  },
  observed: {
    ...dna.observed,
    font_sizes: [
      { size_pt: 12, frequency: 92, roles: ['body'] },
      { size_pt: 18, frequency: 23, roles: ['title'] },
      { size_pt: 13.22, frequency: 16, roles: [] },
    ],
    spacing: { common_margins_emu: [WIDTH * 0.055], common_gaps_emu: null, alignment_lines_x: [0.5], alignment_lines_y: [HEIGHT * 0.25] },
  },
  anchors: [
    { kind: 'page_number', bbox: { x: 0.9, y: 0.91, w: 0.06, h: 0.05 }, slide_coverage: 0.76 },
    { kind: 'logo', bbox: { x: WIDTH * 0.84, y: HEIGHT * 0.04, w: WIDTH * 0.12, h: HEIGHT * 0.08 }, slide_coverage: 0.94 },
  ],
  slide_roles: [
    { role: 'title', slide_ids: ['s1', 's2'], confidence: 0.97, evidence: 'layout' },
    { role: 'content', slide_ids: ['s3', 's4', 's5'], confidence: 0.64 },
    { role: 'unresolved', slide_ids: ['s6'], confidence: 0.1 },
  ],
}

describe('normalizeBox', () => {
  it('keeps normalized boxes and converts EMU boxes to slide fractions', () => {
    expect(normalizeBox({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 }, dna.slide_size)).toEqual({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 })
    const box = normalizeBox({ x: WIDTH / 2, y: HEIGHT / 4, w: WIDTH / 10, h: HEIGHT / 5 }, dna.slide_size)
    expect(box.x).toBeCloseTo(0.5)
    expect(box.y).toBeCloseTo(0.25)
    expect(box.w).toBeCloseTo(0.1)
    expect(box.h).toBeCloseTo(0.2)
  })
})

describe('DNA details on the real, sparse DNA', () => {
  it('returns nothing where the backend sent nothing', () => {
    expect(fontSizeScale(dna)).toEqual([])
    expect(spacingSummary(dna)).toBeNull()
    expect(anchorViews(dna)).toEqual([])
    expect(slideRoles(dna)).toEqual([])
    expect(masterTree(detail, dna)).toEqual([])
  })

  it('still ranks layouts by usage without placeholder geometry', () => {
    const blueprints = layoutBlueprints(detail, dna)
    expect(blueprints).toHaveLength(18)
    expect(blueprints[0]).toMatchObject({ key: '11', name: null, part: layoutPart(11), slides: 36, placeholders: [] })
    expect(blueprints[0]?.share).toBeCloseTo(36 / 54)
    expect(blueprints[1]).toMatchObject({ key: '3', slides: 2 })
  })

  it('summarizes conflicts and gaps from real comparisons only', () => {
    const understanding = understandTemplate(buildDesignSystem(detail, dna), detail, dna)
    expect(understanding.coverage.filter((item) => item.available).map((item) => item.group)).toEqual(['palette', 'fonts', 'layouts'])
    expect(understanding.conflicts[0]).toMatchObject({ kind: 'font', declared: ['Arial'] })
    const fontConflict = understanding.conflicts[0]
    expect(fontConflict?.kind === 'font' && fontConflict.observed.map((font) => font.name)).toEqual(['Play', 'Calibri'])
    expect(understanding.conflicts[1]).toMatchObject({ kind: 'color', color: { hex: '#C4C4C4' } })
    expect(understanding.gaps).toEqual([
      { kind: 'dominant_layout', layout: '11', slides: 36, total: 54 },
      { kind: 'unused_layouts', unused: 21, total: 39 },
      { kind: 'missing', groups: ['sizes', 'spacing', 'anchors', 'roles'] },
    ])
  })
})

describe('DNA details on a filled DNA', () => {
  it('orders the font size scale from large to small and flags fractional sizes', () => {
    const scale = fontSizeScale(rich)
    expect(scale.map((step) => step.pt)).toEqual([18, 13.22, 12])
    expect(scale[2]).toMatchObject({ count: 92, weight: 1, fractional: false, roles: ['body'] })
    expect(scale[1]?.fractional).toBe(true)
  })

  it('converts spacing to slide fractions', () => {
    const spacing = spacingSummary(rich)
    expect(spacing?.margins[0]).toBeCloseTo(0.055)
    expect(spacing?.linesX).toEqual([0.5])
    expect(spacing?.linesY[0]).toBeCloseTo(0.25)
    expect(spacing?.gaps).toEqual([])
  })

  it('orders anchors by coverage', () => {
    const anchors = anchorViews(rich)
    expect(anchors.map((anchor) => anchor.kind)).toEqual(['logo', 'page_number'])
    expect(anchors[0]?.box.x).toBeCloseTo(0.84)
  })

  it('draws placeholders of used and unused layouts', () => {
    const blueprints = layoutBlueprints(detail, rich)
    expect(blueprints[0]).toMatchObject({ key: '11', name: '1_Контент', type: 'obj', slides: 36 })
    expect(blueprints[0]?.placeholders).toHaveLength(1)
    expect(blueprints[0]?.placeholders[0]?.box.w).toBeCloseTo(0.8)
    const unused = blueprints.find((blueprint) => blueprint.name === 'Пустой')
    expect(unused).toMatchObject({ key: 'Пустой', slides: 0, part: layoutPart(19) })
  })

  it('builds the master to layout to slide tree', () => {
    expect(masterTree(detail, rich)).toEqual([
      { part: 'ppt/slideMasters/slideMaster1.xml', layouts: 3, usedLayouts: 2, slides: 37 },
      { part: 'ppt/slideMasters/slideMaster2.xml', layouts: 1, usedLayouts: 0, slides: 0 },
    ])
  })

  it('lists resolved slide roles with confidence', () => {
    expect(slideRoles(rich)).toEqual([
      { role: 'content', slides: 3, confidence: 0.64, evidence: null },
      { role: 'title', slides: 2, confidence: 0.97, evidence: 'layout' },
    ])
  })

  it('reports full coverage when every group is present', () => {
    const understanding = understandTemplate(buildDesignSystem(detail, rich), detail, rich)
    expect(understanding.coverage.every((item) => item.available)).toBe(true)
    expect(understanding.gaps.some((gap) => gap.kind === 'missing')).toBe(false)
  })
})

describe('analysisState', () => {
  it('reads the latest analysis status', () => {
    expect(analysisState(detail)).toBe('completed')
    expect(analysisState({ ...detail, latest_analysis: null })).toBe('missing')
    expect(analysisState(undefined)).toBe('missing')
  })
})
