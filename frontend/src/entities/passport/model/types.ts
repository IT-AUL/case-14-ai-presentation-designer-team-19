import type { ApiError, Schemas } from '@/shared/api'

export type ExportRecord = Schemas['ExportRecord']
export type ExportArtifact = Schemas['ExportArtifact']
export type ExportAccepted = Schemas['ExportAccepted']
export type ExportFormat = Schemas['ExportRequest']['formats'][number]

export type CreateExportResult =
  | { status: 'created'; formats: ExportFormat[]; exportId: string; jobId: string; record: ExportRecord | null }
  | { status: 'unavailable'; formats: ExportFormat[]; message: string; error: ApiError }

export interface PassportInputs {
  templateName: string | null
  templateSha256: string | null
  contentPackId: string | null
  briefHash: string | null
}

export interface PassportValidity {
  opensCleanly: boolean | null
  ooxmlErrors: number | null
  roundTripOk: boolean | null
}

export interface PassportEditability {
  peiLevel: number | null
  nativeTextRatio: number | null
  rasterOnlySlides: number | null
}

export interface PassportContentSupport {
  supportedClaims: number | null
  unsupportedClaims: number | null
  numbersVerified: number | null
  numbersFailed: number | null
}

export interface PassportReadability {
  contrastFailures: number | null
  overflowCount: number | null
  avgOccupancy: number | null
}

export interface PassportStageTiming {
  stage: string
  seconds: number
}

export interface PassportTimings {
  totalSeconds: number | null
  perStage: PassportStageTiming[]
}

export interface PassportScore {
  key: string
  value: number
}

export interface PassportIssues {
  blocker: number
  error: number
  warning: number
  info: number
  unresolved: number | null
}

export interface PassportFallback {
  label: string
  detail: string | null
}

export interface PassportAutoFix {
  ruleCode: string
  count: number | null
  detail: string | null
}

export interface PassportModelProfile {
  role: string | null
  modelId: string | null
  attributes: Record<string, string>
}

export interface PassportProvenance {
  pipelineVersion: string | null
  skillVersion: string | null
  promptVersions: Record<string, string>
  modelProfiles: PassportModelProfile[]
  configHashes: Record<string, string>
}

export interface PassportExport {
  format: string
  artifactId: string
  sha256: string | null
  sizeBytes: number | null
}

export interface QualityPassport {
  schemaVersion: string | null
  runId: string | null
  generatedAt: string | null
  inputs: PassportInputs
  validity: PassportValidity
  editability: PassportEditability
  styleFidelity: PassportScore[] | null
  contentSupport: PassportContentSupport
  readability: PassportReadability
  timings: PassportTimings
  usage: PassportScore[] | null
  issues: PassportIssues
  fallbacks: PassportFallback[]
  autoFixes: PassportAutoFix[]
  provenance: PassportProvenance
  exports: PassportExport[]
}
