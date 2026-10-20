import type { ContentFillSummary, PlanResearch, PlanSufficiency } from '../lib/planExtras'
import type { Schemas } from '@/shared/api'

export type GenerationDetail = Schemas['GenerationDetail']
export type GenerationAccepted = Schemas['GenerationAccepted']
export type GenerationBrief = Schemas['Brief']
export type VariantRequest = Schemas['VariantRequest']
export type Job = Schemas['Job']
export type JobError = Schemas['JobError']
export type JobKind = Schemas['JobKind']
export type JobState = Schemas['JobState']
export type DeckPlan = Schemas['DeckPlan-Output']
export type SlidePlan = Schemas['SlidePlan-Output']
export type SlidePurpose = Schemas['Purpose']
export type DeckPlanSection = Schemas['deckdna__contracts__deck_plan__Section']
export type DeckPlanProvenance = Schemas['deckdna__contracts__deck_plan__Provenance']
export type ContentUnit = Schemas['ContentUnit-Output']
export type GenerationCreate = Schemas['GenerationCreate'] & {
  progressive_previews?: boolean
  execution_mode?: 'fast' | 'checked'
}

export type SlideDraftStatus = 'draft' | 'rendered' | 'superseded' | 'preview_failed' | (string & {})

export interface SlideDraftItem {
  variant_id?: string | null
  index?: number | null
  slide_id?: string | null
  slide_key?: string | null
  revision?: number | null
  preview_revision?: number | null
  preview_artifact_id?: string | null
  status?: SlideDraftStatus | null
  error?: string | null
  updated_at?: string | null
}

export interface SlideDraftsResponse {
  variant_id: string
  state?: JobState | string
  slides: SlideDraftItem[]
}

export type PlanSet = Schemas['PlanSet']

export interface PlanProposal {
  strategy: string | null
  deckPlan: DeckPlan
}

export interface PlanDraftResult {
  planId: string | null
  proposals: PlanProposal[]
  sufficiency: PlanSufficiency | null
  research: PlanResearch | null
  contentFill?: ContentFillSummary | null
}

export type PlanRequest = Omit<Schemas['PlanRequest'], 'run_async'> & { run_async?: boolean }
