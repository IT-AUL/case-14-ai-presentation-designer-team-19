import type { components } from '../schema'

type Schemas = components['schemas']

export type AuditIssue = Schemas['AuditIssue']
export type AuditRun = Schemas['AuditRun']
export type Brief = Schemas['Brief']
export type ContentPack = Schemas['ContentPack']
export type ContentPackAccepted = Schemas['ContentPackAccepted']
export type DesignDna = Schemas['DesignDNAOut']
export type EvidenceGraph = Schemas['EvidenceGraph']
export type ExportArtifact = Schemas['ExportArtifact']
export type ExportRecord = Schemas['ExportRecord']
export type GenerationCreate = Schemas['GenerationCreate']
export type GenerationDetail = Schemas['GenerationDetail']
export type Job = Schemas['Job']
export type JobKind = Schemas['JobKind']
export type JobState = Schemas['JobState']
export type Project = Schemas['Project']
export type ProviderSession = Schemas['ProviderSession']
export type ProviderCapabilities = Schemas['ProviderCapabilities']
export type Severity = Schemas['Severity']
export type SlideInfo = Schemas['SlideInfo']
export type SlidePreviewMeta = Schemas['SlidePreviewMeta']
export type TemplateAnalysis = Schemas['TemplateAnalysis']
export type TemplateAsset = Schemas['TemplateAsset']
export type TemplateDetail = Schemas['TemplateDetail']
export type VariantSummary = Schemas['VariantSummary']

export type ExportFormat = Schemas['ExportRequest']['formats'][number]
export type VariantStrategy = Schemas['VariantRequest']['strategy']
export type FixtureStrategy = Exclude<VariantStrategy, 'custom'>

export interface UploadedFile {
  name: string
  type: string
  bytes: Uint8Array<ArrayBuffer>
}
