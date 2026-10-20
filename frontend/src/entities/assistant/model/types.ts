import type { components } from '@/shared/api'

type Schemas = components['schemas']
export type AssistantStep = 1 | 2 | 3 | 4 | 5 | 6 | 7
export type AssistantSession = Omit<Schemas['AssistantSession'], 'step' | 'completed_steps' | 'skipped_steps'> & {
  step: AssistantStep
  completed_steps: AssistantStep[]
  skipped_steps: AssistantStep[]
}
export type IntakeKind = Schemas['IntakeFilePatch']['kind']
export type Insight = Schemas['Insight']
export type AutopilotRequest = Omit<Schemas['AutopilotRequest'], 'from_step'> & { from_step: AssistantStep }
export type AutopilotAccepted = Schemas['AutopilotAccepted']
export type BlockOps = Schemas['BlockOps']
export type InsightPatch = Schemas['InsightPatch']
export type NotesBody = Schemas['NotesBody']
export type AnswersBody = Schemas['AnswersBody']
export type BrandPriority = Schemas['BrandbookPatch']['priority']
export type PlanSlidePatch = Schemas['SlideChartPatch']
export type PlanSlideAction = Schemas['SlideActionBody']
export type StylePreviewRequest = Schemas['StylePreviewRequest']
export type RegenerateRequest = Schemas['RegenerateRequest']
export type PlanSet = Schemas['PlanSet']
export type SlideInfo = Schemas['SlideInfo']
export type JsonRecord = Record<string, unknown>

export interface IntakeFile {
  name: string
  detected_kind: IntakeKind
  kind: IntakeKind
  accepted: boolean
  reason: string | null
  size_bytes: number | null
}
export interface IntakeResult {
  project_id: string | null
  job_id: string | null
  template_id: string | null
  content_pack_id: string | null
  brandbook_id: string | null
  files: IntakeFile[]
  assistant_note: string | null
}
export interface TemplateSummary {
  template_id: string | null
  filename: string | null
  slides: number | null
  roles: Record<string, number>
  palette: string[]
  fonts: string[]
  heading_font: string | null
  body_font: string | null
  preview_artifact_ids: string[]
  assistant_note: string | null
}
export interface BrandColor extends JsonRecord {
  name: string | null
  hex: string | null
  role: string | null
  source: string | null
}
export interface BrandFont extends JsonRecord {
  role: string | null
  family: string | null
}
export interface BrandConflict extends JsonRecord {
  field: string | null
  kind: string | null
  template: string | null
  brandbook: string | null
  resolved: string | null
  note: string | null
}
export interface ChartChoice extends JsonRecord {
  type: string | null
  reasons: string[]
}
export interface ChartRecommendation extends JsonRecord {
  chart_type: string | null
  score: number | null
  reasons: string[]
}
export interface DataPreview extends JsonRecord {
  chart_type: string | null
  categories: string[]
  series: { name: string | null; values: (number | null)[] }[]
  unit: string | null
}
export interface BrandbookSummary {
  id: string | null
  name: string | null
  palette: BrandColor[]
  fonts: BrandFont[]
  logos: (JsonRecord & { file_name: string | null; artifact_id: string | null })[]
  rules: unknown[]
  sources: unknown[]
  warnings: string[]
  conflicts: BrandConflict[]
  priority: BrandPriority | null
  assistant_note: string | null
}
export interface UnderstandingBlock extends JsonRecord {
  id: string
  kind: string
  title: string | null
  text: string | null
  source_ref: JsonRecord | null
  dataset_id: string | null
  preview: DataPreview | null
}
export interface UnderstandingDataset extends JsonRecord {
  id: string
  title: string | null
  file: string | null
  profile_summary: string | null
  task: string | null
  task_ru: string | null
  recommendations: ChartRecommendation[]
  why: string | null
  insights: Insight[]
}
export interface Understanding extends JsonRecord {
  content_pack_id: string | null
  blocks: UnderstandingBlock[]
  summary: { capacity_slides: number | null; main_points: string[]; assistant_note: string | null }
  datasets: UnderstandingDataset[]
  brief: JsonRecord
  context: JsonRecord | null
  interpretation: JsonRecord | null
  assistant_note: string | null
}
export interface PackEditResult {
  content_pack_id: string | null
  filled_from: string | null
  understanding: Understanding | null
}
export interface QuestionOption {
  value: string
  label: string
  recommended: boolean
  note: string | null
}
export interface Question {
  id: string
  kind: string | null
  reason: string | null
  options: QuestionOption[]
  allow_custom: boolean
  optional: boolean
}
export interface QuestionsResult {
  questions: Question[]
  suggested_brief: JsonRecord
  deferred_questions: string[]
  assistant_note: string | null
}
export interface AnswersResult {
  brief: JsonRecord
  follow_up: JsonRecord
  assistant_note: string | null
}
export interface StylePreviewAccepted {
  preview_id: string | null
  job_id: string | null
  events_url: string | null
}
export interface StylePreviewImage {
  slide_id: string
  index: number | null
  exemplar: string | null
  image_artifact_id: string | null
}
export interface PreviewStyle {
  strategy: string
  title: string | null
  difference: string | null
  recommended: boolean
  previews: StylePreviewImage[]
  exemplars: JsonRecord
  seconds: number | null
  error: unknown
}
export interface StylePreview {
  preview_id: string | null
  plan_id: string | null
  template_id: string | null
  slide_ids: string[]
  recommended: string | null
  assistant_note: string | null
  seconds: number | null
  styles: PreviewStyle[]
}
export interface SlideExplain extends JsonRecord {
  slide_id: string | null
  slide_plan_id: string | null
  title: string | null
  dataset: (JsonRecord & { id: string | null; title: string | null; file: string | null; rows: number | null }) | null
  profile_summary: string | null
  task: string | null
  chosen: ChartChoice | null
  alternatives: ChartChoice[]
  rejected: ChartChoice[]
  insights: unknown[]
  assistant_note: string | null
}
export interface RegenerateResult {
  variant_id: string | null
  slide: SlideInfo | null
  deck_revision: number | null
  deck_artifact_id: string | null
  exemplar: string | null
  preview_artifact_id: string | null
  issues: JsonRecord[]
  assistant_note: string | null
}
export interface JobEvent extends JsonRecord {
  sequence: number
  type: string
  timestamp: string | null
  stage: string | null
  progress: number | null
  message: string | null
  data: JsonRecord
}
export type CommandStatus = 'applied' | 'accepted' | 'clarification'
export type CommandParser = 'rules' | 'llm' | string
export interface CommandBriefEdit {
  field: string
  value: unknown
  label: string | null
}
export interface ParsedCommand extends JsonRecord {
  intent: string | null
  action: string | null
  text: string
  chart_type: string | null
  brief: CommandBriefEdit[]
  question: string | null
}
export interface CommandChange extends JsonRecord {
  id: string | null
  job_id: string | null
  variant_id: string | null
  slide_id: string | null
  expected_revision: number | null
  target_revision: number | null
  action: string | null
  state: string | null
  events_url: string | null
}
export interface CommandOption {
  value: string
  label: string
}
export interface AssistantCommandRequest {
  text: string
  variant_id?: string | null
  slide_id?: string | null
  plan_id?: string | null
  expected_revision?: number | null
  expected_context_revision?: number | null
  provider_session_id?: string | null
  use_llm?: boolean
}
export interface AssistantCommandResult extends JsonRecord {
  status: CommandStatus
  parser: CommandParser | null
  command: ParsedCommand | null
  assistant_note: string | null
  brief: JsonRecord | null
  context_revision: number | null
  changed_fields: string[]
  invalidated: string[]
  change: CommandChange | null
  plan: PlanSet | null
  options: CommandOption[]
}

