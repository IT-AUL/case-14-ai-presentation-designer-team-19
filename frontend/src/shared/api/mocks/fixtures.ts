import contentPackJson from './fixtures/content-pack.json'
import designDnaJson from './fixtures/design-dna.json'
import generationJson from './fixtures/generation.json'
import generationJobJson from './fixtures/job-generation.json'
import passportJson from './fixtures/passport.json'
import projectJson from './fixtures/project.json'
import repairJobJson from './fixtures/repair-job.json'
import templateDetailJson from './fixtures/template-detail.json'
import variantsJson from './fixtures/variants.json'
import type {
  AuditIssue,
  AuditRun,
  ContentPackAccepted,
  DesignDna,
  ExportRecord,
  FixtureStrategy,
  GenerationDetail,
  Job,
  Project,
  SlideInfo,
  TemplateDetail,
  VariantSummary,
} from './types'

export interface VariantFixture {
  variant: VariantSummary
  slides: SlideInfo[]
  audit: AuditRun
  issues: AuditIssue[]
  export: ExportRecord
}

export type QualityPassport = typeof passportJson

export const FIXTURE_STRATEGIES: readonly FixtureStrategy[] = ['faithful', 'balanced', 'visual']

export const projectFixture = projectJson as Project
export const templateDetailFixture = templateDetailJson as TemplateDetail
export const designDnaFixture = designDnaJson as DesignDna
export const contentPackFixture = contentPackJson as ContentPackAccepted
export const generationFixture = generationJson as GenerationDetail
export const generationJobFixture = generationJobJson as Job
export const repairJobFixture = repairJobJson as Job
export const passportFixture: QualityPassport = passportJson
export const variantFixtures = variantsJson as Record<FixtureStrategy, VariantFixture>
