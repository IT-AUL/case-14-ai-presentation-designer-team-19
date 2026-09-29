import type { Schemas } from '@/shared/api'

export type VariantAxes = Schemas['VariantAxes']
export type VariantMetrics = Schemas['VariantMetrics']

export type VariantSummary = Schemas['VariantSummary']
export type VariantStatus = VariantSummary['status']
export type VariantAuditStatus = VariantSummary['audit_status']
export type SlideInfo = Schemas['SlideInfo']
export type ExportRecord = Schemas['ExportRecord']
export type ExportArtifact = Schemas['ExportArtifact']
export type VariantStrategy = Schemas['VariantRequest']['strategy']
export type CatalogStrategy = Exclude<VariantStrategy, 'custom'>
