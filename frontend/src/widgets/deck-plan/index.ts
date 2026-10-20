export {
  availableQuickActions,
  buildPlanView,
  CHART_TYPE_LABEL,
  chartTypeLabel,
  contentPackSourceMap,
  deckPurposeLabel,
  DEFAULT_CHART_ALTERNATIVES,
  DEFAULT_CHART_REASONS,
  EDITOR_VISUALS,
  formatSlideRange,
  normalizeChartReasonText,
  ORIGIN_LABEL,
  parseEvidenceRef,
  parseSlideChartView,
  plannedVisual,
  resolveDeckKeyMessage,
  slideIdea,
  slideSourceKinds,
  slideSources,
  SOURCE_KIND_LABEL,
  VISUAL_LABEL,
} from './lib/plan'
export type { EvidenceRef, PlanRow, PlanSectionSummary, PlanStats, PlanView, QuickActionKind, SlideChartOption, SlideChartView, SlideSource, SourceKind, SourcePackLike, SourcePlanLike } from './lib/plan'
export { ChartMiniSvg, ChartReasoningBox } from './ui/ChartReasoningBox'
export { PlanCard } from './ui/PlanCard'
export { DeckPlanView } from './ui/DeckPlanView'
export type { PlanEditor } from './ui/DeckPlanView'
export { KeyMessageBanner } from './ui/KeyMessageBanner'
