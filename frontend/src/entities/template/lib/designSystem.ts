import { colorDistance } from './color'
import type { DesignDna, PackageInventory, TemplateDetail } from '../model/types'

const EMU_PER_CM = 360000
const NEAR_COLOR_DISTANCE = 24

export const THEME_SLOTS = ['dk1', 'lt1', 'dk2', 'lt2', 'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6', 'hlink', 'folHlink'] as const

export interface PaletteSlot {
  slot: string
  hex: string
}

export interface UsedColor {
  hex: string
  count: number
  share: number
  match: { kind: 'theme' | 'near' | 'off'; slot?: string }
}

export interface FontUsage {
  name: string
  count: number
  share: number
}

export interface LayoutUsage {
  layout: string
  slides: number
  share: number
}

export interface DesignSystem {
  fileName: string
  sizeBytes: number
  sha256: string
  aspectLabel: string
  sizeLabel: string
  counts: Required<Pick<PackageInventory, 'slides' | 'masters' | 'layouts' | 'themes' | 'media' | 'charts' | 'tables'>>
  palette: PaletteSlot[]
  paletteName: string | null
  usedColors: UsedColor[]
  offPaletteShare: number
  declaredFonts: string[]
  observedFonts: FontUsage[]
  primaryFont: string | null
  fontConflict: boolean
  layoutUsage: LayoutUsage[]
  dominantLayoutShare: number
  unusedLayouts: number
  unsupported: DesignDna['unsupported_features']
}

function aspectLabel(ratio: number): string {
  if (Math.abs(ratio - 16 / 9) < 0.01) return '16:9'
  if (Math.abs(ratio - 4 / 3) < 0.01) return '4:3'
  if (Math.abs(ratio - 16 / 10) < 0.01) return '16:10'
  return ratio.toFixed(2).replace('.', ',')
}

function toHex(value: string): string {
  return `#${value.replace('#', '').toUpperCase()}`
}

function matchColor(hex: string, palette: PaletteSlot[]): UsedColor['match'] {
  let best: { slot: string; distance: number } | null = null
  for (const entry of palette) {
    const distance = colorDistance(hex, entry.hex)
    if (!best || distance < best.distance) best = { slot: entry.slot, distance }
  }
  if (!best) return { kind: 'off' }
  if (best.distance === 0) return { kind: 'theme', slot: best.slot }
  if (best.distance <= NEAR_COLOR_DISTANCE) return { kind: 'near', slot: best.slot }
  return { kind: 'off' }
}

export function buildDesignSystem(detail: TemplateDetail, dna: DesignDna | undefined): DesignSystem {
  const inventory = (detail.latest_analysis?.package_inventory ?? {}) as PackageInventory
  const [paletteName, paletteMap]: [string | null, Record<string, string>] = Object.entries(inventory.theme_palettes ?? {})[0] ?? [null, {}]
  const palette = THEME_SLOTS.flatMap((slot) => (paletteMap[slot] ? [{ slot, hex: toHex(paletteMap[slot]) }] : []))

  const colorEntries = Object.entries(inventory.observed_colors ?? {}).sort(([, a], [, b]) => b - a)
  const colorTotal = colorEntries.reduce((sum, [, count]) => sum + count, 0) || 1
  const usedColors = colorEntries.map(([hex, count]) => {
    const normalized = toHex(hex)
    return { hex: normalized, count, share: count / colorTotal, match: matchColor(normalized, palette) }
  })

  const fontEntries = Object.entries(inventory.observed_fonts ?? {}).sort(([, a], [, b]) => b - a)
  const fontTotal = fontEntries.reduce((sum, [, count]) => sum + count, 0) || 1
  const observedFonts = fontEntries.map(([name, count]) => ({ name, count, share: count / fontTotal }))
  const declaredFonts = inventory.declared_fonts ?? [...new Set((dna?.declared.themes ?? []).flatMap((theme) => [theme.major_font, theme.minor_font]))]
  const primaryFont = observedFonts[0]?.name ?? declaredFonts[0] ?? null

  const slides = inventory.slides ?? dna?.exemplars.length ?? 0
  const layoutUsage = Object.entries(inventory.layout_usage ?? {})
    .map(([layout, count]) => ({ layout, slides: count, share: slides ? count / slides : 0 }))
    .sort((a, b) => b.slides - a.slides)
  const layoutsTotal = inventory.layouts ?? dna?.declared.layouts.length ?? 0

  const width = dna?.slide_size.width_emu ?? 0
  const height = dna?.slide_size.height_emu ?? 0

  return {
    fileName: detail.filename,
    sizeBytes: detail.size_bytes,
    sha256: detail.sha256,
    aspectLabel: dna ? aspectLabel(dna.slide_size.aspect_ratio) : '—',
    sizeLabel: width && height ? `${(width / EMU_PER_CM).toFixed(2).replace('.', ',')} × ${(height / EMU_PER_CM).toFixed(2).replace('.', ',')} см` : '—',
    counts: {
      slides,
      masters: inventory.masters ?? dna?.declared.masters.length ?? 0,
      layouts: layoutsTotal,
      themes: inventory.themes ?? dna?.declared.themes.length ?? 0,
      media: inventory.media ?? 0,
      charts: inventory.charts ?? 0,
      tables: inventory.tables ?? 0,
    },
    palette,
    paletteName,
    usedColors,
    offPaletteShare: usedColors.filter((color) => color.match.kind === 'off').reduce((sum, color) => sum + color.share, 0),
    declaredFonts,
    observedFonts,
    primaryFont,
    fontConflict: Boolean(primaryFont && declaredFonts.length && !declaredFonts.includes(primaryFont)),
    layoutUsage,
    dominantLayoutShare: layoutUsage[0]?.share ?? 0,
    unusedLayouts: Math.max(0, layoutsTotal - layoutUsage.length),
    unsupported: dna?.unsupported_features ?? [],
  }
}
