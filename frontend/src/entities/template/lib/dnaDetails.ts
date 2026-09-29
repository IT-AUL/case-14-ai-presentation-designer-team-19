import type { DesignDna, PackageInventory, TemplateDetail } from '../model/types'
import type { DesignSystem, FontUsage, UsedColor } from './designSystem'

type Anchor = DesignDna['anchors'][number]
type Bbox = Anchor['bbox']
type SlideSize = DesignDna['slide_size']

export type AnchorKind = Anchor['kind']
export type UnsupportedStrategy = DesignDna['unsupported_features'][number]['strategy']

export interface NormalizedBox {
  x: number
  y: number
  w: number
  h: number
}

export interface FontSizeStep {
  pt: number
  count: number
  weight: number
  roles: string[]
  fractional: boolean
}

export interface SpacingSummary {
  margins: number[]
  gaps: number[]
  linesX: number[]
  linesY: number[]
}

export interface AnchorView {
  kind: AnchorKind
  box: NormalizedBox
  coverage: number
}

export interface PlaceholderBox {
  type: string
  box: NormalizedBox
}

export interface LayoutBlueprint {
  key: string
  name: string | null
  type: string | null
  part: string | null
  slides: number
  share: number
  placeholders: PlaceholderBox[]
}

export interface MasterBranch {
  part: string
  layouts: number
  usedLayouts: number
  slides: number
}

export interface RoleView {
  role: string
  slides: number
  confidence: number
  evidence: string | null
}

export type DnaGroup = 'palette' | 'fonts' | 'sizes' | 'spacing' | 'anchors' | 'layouts' | 'roles'

export interface GroupCoverage {
  group: DnaGroup
  available: boolean
}

export type UnderstandingConflict =
  | { kind: 'font'; declared: string[]; observed: FontUsage[] }
  | { kind: 'color'; color: UsedColor; offShare: number }

export type UnderstandingGap =
  | { kind: 'unused_layouts'; unused: number; total: number }
  | { kind: 'dominant_layout'; layout: string; slides: number; total: number }
  | { kind: 'missing'; groups: DnaGroup[] }
  | { kind: 'warning'; text: string }

export interface Understanding {
  coverage: GroupCoverage[]
  conflicts: UnderstandingConflict[]
  gaps: UnderstandingGap[]
}

const UNRESOLVED = 'unresolved'
const DOMINANT_LAYOUT_SHARE = 0.5
const OFF_PALETTE_NOTICE_SHARE = 0.05

function isFraction(value: number): boolean {
  return value >= -0.5 && value <= 1.5
}

export function normalizeBox(box: Bbox, size: SlideSize): NormalizedBox {
  const values = [box.x, box.y, box.w, box.h]
  if (values.every(isFraction)) return { x: box.x, y: box.y, w: box.w, h: box.h }
  const width = size.width_emu || 1
  const height = size.height_emu || 1
  return { x: box.x / width, y: box.y / height, w: box.w / width, h: box.h / height }
}

function toFraction(value: number, extent: number): number {
  return isFraction(value) ? value : value / (extent || 1)
}

function resolved(value: string | null | undefined): string | null {
  return value && value !== UNRESOLVED ? value : null
}

function inventoryOf(detail: TemplateDetail): PackageInventory {
  return (detail.latest_analysis?.package_inventory ?? {}) as PackageInventory
}

export function fontSizeScale(dna: DesignDna | undefined): FontSizeStep[] {
  const sizes = dna?.observed.font_sizes ?? []
  const max = Math.max(1, ...sizes.map((size) => size.frequency))
  return [...sizes]
    .sort((a, b) => b.size_pt - a.size_pt)
    .map((size) => ({
      pt: size.size_pt,
      count: size.frequency,
      weight: size.frequency / max,
      roles: size.roles,
      fractional: !Number.isInteger(size.size_pt * 2),
    }))
}

export function spacingSummary(dna: DesignDna | undefined): SpacingSummary | null {
  if (!dna) return null
  const { spacing } = dna.observed
  const width = dna.slide_size.width_emu
  const height = dna.slide_size.height_emu
  const summary: SpacingSummary = {
    margins: (spacing.common_margins_emu ?? []).map((value) => toFraction(value, width)),
    gaps: (spacing.common_gaps_emu ?? []).map((value) => toFraction(value, width)),
    linesX: (spacing.alignment_lines_x ?? []).map((value) => toFraction(value, width)),
    linesY: (spacing.alignment_lines_y ?? []).map((value) => toFraction(value, height)),
  }
  const empty = Object.values(summary).every((list) => list.length === 0)
  return empty ? null : summary
}

export function anchorViews(dna: DesignDna | undefined): AnchorView[] {
  if (!dna) return []
  return [...dna.anchors]
    .sort((a, b) => b.slide_coverage - a.slide_coverage)
    .map((anchor) => ({ kind: anchor.kind, box: normalizeBox(anchor.bbox, dna.slide_size), coverage: anchor.slide_coverage }))
}

function partNumber(part: string): string | null {
  return /(\d+)\.xml$/.exec(part)?.[1] ?? null
}

export function layoutBlueprints(detail: TemplateDetail, dna: DesignDna | undefined): LayoutBlueprint[] {
  const inventory = inventoryOf(detail)
  const declared = dna?.declared.layouts ?? []
  const totalSlides = inventory.slides ?? dna?.exemplars.length ?? 0
  const usage = inventory.layout_usage ?? {}
  const byKey = new Map<string, (typeof declared)[number]>()
  for (const layout of declared) {
    const name = resolved(layout.name)
    if (name) byKey.set(name, layout)
    const number = partNumber(layout.part)
    if (number && !byKey.has(number)) byKey.set(number, layout)
  }

  const toBlueprint = (key: string, layout: (typeof declared)[number] | undefined, slides: number): LayoutBlueprint => ({
    key,
    name: resolved(layout?.name),
    type: resolved(layout?.type),
    part: layout?.part ?? null,
    slides,
    share: totalSlides ? slides / totalSlides : 0,
    placeholders: dna
      ? (layout?.placeholders ?? []).flatMap((placeholder) =>
          placeholder.bbox ? [{ type: placeholder.type, box: normalizeBox(placeholder.bbox, dna.slide_size) }] : [],
        )
      : [],
  })

  const used = Object.entries(usage).map(([key, slides]) => toBlueprint(key, byKey.get(key), slides))
  const usedParts = new Set(used.flatMap((blueprint) => (blueprint.part ? [blueprint.part] : [])))
  const unused = declared
    .filter((layout) => !usedParts.has(layout.part) && layout.placeholders.some((placeholder) => placeholder.bbox))
    .map((layout) => toBlueprint(resolved(layout.name) ?? partNumber(layout.part) ?? layout.part, layout, 0))

  return [...used, ...unused].sort((a, b) => b.slides - a.slides || a.key.localeCompare(b.key, 'ru', { numeric: true }))
}

export function masterTree(detail: TemplateDetail, dna: DesignDna | undefined): MasterBranch[] {
  const masters = dna?.declared.masters ?? []
  if (!masters.some((master) => master.layout_parts.length)) return []
  const blueprints = layoutBlueprints(detail, dna)
  const slidesByPart = new Map(blueprints.flatMap((blueprint) => (blueprint.part ? [[blueprint.part, blueprint.slides] as const] : [])))
  return masters.map((master) => {
    const counts = master.layout_parts.map((part) => slidesByPart.get(part) ?? 0)
    return {
      part: master.part,
      layouts: master.layout_parts.length,
      usedLayouts: counts.filter(Boolean).length,
      slides: counts.reduce((sum, count) => sum + count, 0),
    }
  })
}

export function slideRoles(dna: DesignDna | undefined): RoleView[] {
  return [...(dna?.slide_roles ?? [])]
    .filter((role) => resolved(role.role))
    .map((role) => ({ role: role.role, slides: role.slide_ids.length, confidence: role.confidence, evidence: role.evidence ?? null }))
    .sort((a, b) => b.slides - a.slides)
}

export function understandTemplate(system: DesignSystem, detail: TemplateDetail, dna: DesignDna | undefined): Understanding {
  const available: Record<DnaGroup, boolean> = {
    palette: system.palette.length > 0 || system.usedColors.length > 0,
    fonts: system.declaredFonts.length > 0 || system.observedFonts.length > 0,
    sizes: fontSizeScale(dna).length > 0,
    spacing: spacingSummary(dna) !== null,
    anchors: anchorViews(dna).length > 0,
    layouts: system.layoutUsage.length > 0 || layoutBlueprints(detail, dna).some((blueprint) => blueprint.placeholders.length),
    roles: slideRoles(dna).length > 0,
  }
  const groups = Object.keys(available) as DnaGroup[]
  const coverage = groups.map((group) => ({ group, available: available[group] }))

  const conflicts: UnderstandingConflict[] = []
  if (system.fontConflict) {
    conflicts.push({
      kind: 'font',
      declared: system.declaredFonts,
      observed: system.observedFonts.filter((font) => !system.declaredFonts.includes(font.name)).slice(0, 2),
    })
  }
  const offColor = system.usedColors.find((color) => color.match.kind === 'off')
  if (offColor && system.offPaletteShare >= OFF_PALETTE_NOTICE_SHARE) {
    conflicts.push({ kind: 'color', color: offColor, offShare: system.offPaletteShare })
  }

  const gaps: UnderstandingGap[] = []
  const dominant = system.layoutUsage[0]
  if (dominant && system.counts.slides && dominant.share >= DOMINANT_LAYOUT_SHARE) {
    gaps.push({ kind: 'dominant_layout', layout: dominant.layout, slides: dominant.slides, total: system.counts.slides })
  }
  if (system.unusedLayouts > 0) {
    gaps.push({ kind: 'unused_layouts', unused: system.unusedLayouts, total: system.counts.layouts })
  }
  const missing = groups.filter((group) => !available[group])
  if (missing.length) gaps.push({ kind: 'missing', groups: missing })
  for (const text of detail.latest_analysis?.warnings ?? []) gaps.push({ kind: 'warning', text })

  return { coverage, conflicts, gaps }
}
