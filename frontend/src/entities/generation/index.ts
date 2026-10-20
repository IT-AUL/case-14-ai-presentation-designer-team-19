export { generationKeys, useCancelGeneration, useGeneration, useJob, useRetryGeneration, useSlideDrafts } from './api/generationApi'
export { fetchSlideDrafts } from './api/requests'
export { parsePlanDraft, PLAN_POLL_INTERVAL_MS, requestPlans } from './api/plans'
export type { PlanRequestOptions } from './api/plans'
export {
  applyPlanEvent,
  EMPTY_PROGRESS,
  formatFactsByOriginText,
  formatShortageExplanationText,
  isUnitLocked,
  parseContentFill,
  parseResearch,
  parseSse,
  parseSufficiency,
  unitOrigin,
  unitSourceId,
} from './lib/planExtras'
export type { ContentFillSummary, FactsByOrigin, PlanProgress, PlanResearch, PlanSufficiency, ResearchSource, SufficiencyStatus, UnitOrigin } from './lib/planExtras'
export { budgetStatus, elapsedSeconds } from './lib/budget'
export type { BudgetStatus, GenerationTiming } from './lib/budget'
export { CONTENT_FILL_MODES, contentFillValue, isContentFillMode, suggestedContentFill } from './lib/contentFill'
export type { ContentFillMode, ContentFillOption } from './lib/contentFill'
export { EDITABLE_UNIT_KINDS, hasChartData, isEditableUnit } from './lib/units'
export { SLIDE_PURPOSE_LABEL, slidePurposeLabel } from './lib/purpose'
export { GENERATION_PIPELINE, jobStatusLabel, PIPELINE_STAGE_LABEL, pipelineStage, stageLabel } from './lib/stages'
export type { PipelineStage } from './lib/stages'
export type { GenerationPhase } from './model/phase'
export { isTerminalState, JOB_STATE_LABEL, POLL_INTERVAL_MS, TERMINAL_JOB_STATES } from './model/state'
export { getTrackedGeneration, isTrackingId, retryGeneration, startGeneration, whenGenerationAccepted } from './model/tracker'
export type { SubmissionStatus, TrackedGeneration } from './model/tracker'
export { useGenerationTracker } from './model/useGenerationTracker'
export type { GenerationTracker } from './model/useGenerationTracker'
export type {
  ContentUnit,
  DeckPlan,
  DeckPlanProvenance,
  DeckPlanSection,
  GenerationAccepted,
  GenerationBrief,
  GenerationCreate,
  GenerationDetail,
  Job,
  JobError,
  JobKind,
  JobState,
  PlanDraftResult,
  PlanProposal,
  PlanRequest,
  SlideDraftItem,
  SlideDraftsResponse,
  SlideDraftStatus,
  SlidePlan,
  SlidePurpose,
  VariantRequest,
} from './model/types'
