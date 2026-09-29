export { useVariant, useVariants, useVariantSlides, variantKeys } from './api/variantApi'
export { resolveDeckFiles, slidePreviewUrl } from './lib/artifacts'
export type { DeckFile, DeckFileFormat, DeckFiles } from './lib/artifacts'
export { AUDIT_STATUS_LABEL, PEI_MAX, variantMetrics } from './lib/metrics'
export { telltaleSlides } from './lib/slides'
export type { VariantMetricsView } from './lib/metrics'
export { isVariantSettled } from './model/status'
export {
  DEFAULT_STRATEGY,
  orderVariants,
  STRATEGIES,
  STRATEGY_AXIS,
  STRATEGY_ORDER,
  STRATEGY_PROFILE_LABEL,
  strategyInfo,
  variantAxes,
  variantRationale,
} from './model/strategies'
export type { AxisView, ProfileLevel, StrategyInfo, StrategyProfile } from './model/strategies'
export type {
  CatalogStrategy,
  ExportArtifact,
  ExportRecord,
  SlideInfo,
  VariantAuditStatus,
  VariantAxes,
  VariantMetrics,
  VariantStatus,
  VariantStrategy,
  VariantSummary,
} from './model/types'
