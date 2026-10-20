export { describeFailure, failureMeta, isTrackingLost } from './lib/failure'
export type { FailureView } from './lib/failure'
export { buildFilmstripSlots, buildStageSummary, extractEventGenerationId, latestAssistantNote } from './lib/filmstrip'
export type {
  BuildFilmstripSlotsOptions,
  BuildStageItem,
  BuildStageItemState,
  BuildStageSummary,
  FilmstripSlot,
  FilmstripSlotState,
  SlidePreviewBadge,
} from './lib/filmstrip'
export { GenerationProgress } from './ui/GenerationProgress'
export { RunNotice } from './ui/RunNotice'
export { SlideFilmstrip } from './ui/SlideFilmstrip'
export { BuildSlideInspector, BuildTable, BuildVariants } from './ui/BuildView'
export { useBuildCards, useBuildSlots, useSelectedBuildKey } from './model/buildSelection'
