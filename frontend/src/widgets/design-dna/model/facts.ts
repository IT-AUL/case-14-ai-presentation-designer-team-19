import {
  anchorViews,
  buildDesignSystem,
  fontSizeScale,
  layoutBlueprints,
  masterTree,
  slideRoles,
  spacingSummary,
  understandTemplate,
  type AnchorView,
  type DesignDna,
  type DesignSystem,
  type FontSizeStep,
  type LayoutBlueprint,
  type MasterBranch,
  type RoleView,
  type SpacingSummary,
  type TemplateDetail,
  type Understanding,
} from '@/entities/template'

export interface DnaFacts {
  scale: FontSizeStep[]
  spacing: SpacingSummary | null
  anchors: AnchorView[]
  blueprints: LayoutBlueprint[]
  tree: MasterBranch[]
  roles: RoleView[]
  unsupported: DesignDna['unsupported_features']
  aspectRatio: number
}

export interface DnaView {
  system: DesignSystem
  facts: DnaFacts
  understanding: Understanding
}

export function buildDnaView(detail: TemplateDetail, dna: DesignDna | undefined): DnaView {
  const system = buildDesignSystem(detail, dna)
  const ratio = dna?.slide_size.aspect_ratio
  return {
    system,
    understanding: understandTemplate(system, detail, dna),
    facts: {
      scale: fontSizeScale(dna),
      spacing: spacingSummary(dna),
      anchors: anchorViews(dna),
      blueprints: layoutBlueprints(detail, dna),
      tree: masterTree(detail, dna),
      roles: slideRoles(dna),
      unsupported: dna?.unsupported_features ?? [],
      aspectRatio: ratio && Number.isFinite(ratio) && ratio > 0 ? ratio : 16 / 9,
    },
  }
}
