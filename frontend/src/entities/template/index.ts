export { analyzeTemplate, templateKeys, uploadTemplate, useDesignDna, useTemplate } from './api/templateApi'
export { analysisState } from './lib/analysis'
export type { AnalysisState } from './lib/analysis'
export { isLightColor } from './lib/color'
export { buildDesignSystem, THEME_SLOTS } from './lib/designSystem'
export type { DesignSystem, FontUsage, LayoutUsage, PaletteSlot, UsedColor } from './lib/designSystem'
export {
  anchorViews,
  fontSizeScale,
  layoutBlueprints,
  masterTree,
  normalizeBox,
  slideRoles,
  spacingSummary,
  understandTemplate,
} from './lib/dnaDetails'
export type {
  AnchorKind,
  AnchorView,
  DnaGroup,
  FontSizeStep,
  GroupCoverage,
  LayoutBlueprint,
  MasterBranch,
  NormalizedBox,
  PlaceholderBox,
  RoleView,
  SpacingSummary,
  Understanding,
  UnderstandingConflict,
  UnderstandingGap,
  UnsupportedStrategy,
} from './lib/dnaDetails'
export type { DesignDna, PackageInventory, TemplateAsset, TemplateDetail } from './model/types'
