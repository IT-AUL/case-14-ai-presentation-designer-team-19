import { API_BASE } from '@/shared/config'
import { notFound } from './http'
import { nowIso, prefixedId, sha256Hex } from './ids'
import type {
  AuditIssue,
  AuditRun,
  ContentPack,
  DesignDna,
  ExportArtifact,
  ExportRecord,
  FixtureStrategy,
  GenerationCreate,
  GenerationDetail,
  Job,
  JobKind,
  JobState,
  Project,
  ProviderSession,
  SlideInfo,
  TemplateAnalysis,
  TemplateAsset,
  VariantSummary,
} from './types'

export type ArtifactBody = { kind: 'bytes'; bytes: Uint8Array<ArrayBuffer> } | { kind: 'fixture-pdf'; strategy: FixtureStrategy }

export interface ArtifactRecord {
  id: string
  filename: string
  mimeType: string
  sha256: string
  sizeBytes: number
  body: ArtifactBody
}

export interface TemplateRecord {
  asset: TemplateAsset
  analysis: TemplateAnalysis | null
  dna: DesignDna | null
}

export interface ContentPackRecord {
  projectId: string
  pack: ContentPack
}

export type RunDetail = Omit<GenerationDetail, 'variants'>

export interface RunRecord {
  detail: RunDetail
  request: GenerationCreate
  variantIds: string[]
}

export interface VariantRecord {
  summary: VariantSummary
  fixture: FixtureStrategy
  slides: SlideInfo[]
  auditId: string | null
  revision: number
}

export interface AuditRecord {
  run: AuditRun
  issues: AuditIssue[]
}

export class MockStore {
  readonly projects = new Map<string, Project>()
  readonly templates = new Map<string, TemplateRecord>()
  readonly contentPacks = new Map<string, ContentPackRecord>()
  readonly jobs = new Map<string, Job>()
  readonly runs = new Map<string, RunRecord>()
  readonly variants = new Map<string, VariantRecord>()
  readonly slides = new Map<string, SlideInfo>()
  readonly audits = new Map<string, AuditRecord>()
  readonly issueAudits = new Map<string, string>()
  readonly exports = new Map<string, ExportRecord>()
  readonly artifacts = new Map<string, ArtifactRecord>()
  readonly sessions = new Map<string, ProviderSession>()

  project(id: string): Project {
    return required(this.projects.get(id), 'Project', id)
  }

  template(id: string): TemplateRecord {
    return required(this.templates.get(id), 'Template', id)
  }

  contentPack(id: string): ContentPackRecord {
    return required(this.contentPacks.get(id), 'Content pack', id)
  }

  job(id: string): Job {
    return required(this.jobs.get(id), 'Job', id)
  }

  run(id: string): RunRecord {
    return required(this.runs.get(id), 'Generation', id)
  }

  variant(id: string): VariantRecord {
    return required(this.variants.get(id), 'Variant', id)
  }

  slide(id: string): SlideInfo {
    return required(this.slides.get(id), 'Slide', id)
  }

  audit(id: string): AuditRecord {
    return required(this.audits.get(id), 'Audit', id)
  }

  exportRecord(id: string): ExportRecord {
    return required(this.exports.get(id), 'Export', id)
  }

  artifact(id: string): ArtifactRecord {
    return required(this.artifacts.get(id), 'Artifact', id)
  }

  session(id: string): ProviderSession {
    const session = this.sessions.get(id)
    if (!session || Date.parse(session.expires_at) <= Date.now()) {
      if (session) this.sessions.delete(id)
      throw notFound('Provider session', id)
    }
    return session
  }

  touchProject(id: string, patch: Partial<Project>): void {
    const project = this.projects.get(id)
    if (project) this.projects.set(id, { ...project, ...patch, updated_at: nowIso() })
  }

  createJob(kind: JobKind, projectId: string | null, options: { state?: JobState; resultIds?: Record<string, string>; startedAt?: string } = {}): Job {
    const startedAt = options.startedAt ?? nowIso()
    const state = options.state ?? 'completed'
    const finished = state === 'completed' || state === 'failed' || state === 'canceled'
    const job: Job = {
      id: prefixedId('job'),
      kind,
      state,
      stage: null,
      progress: state === 'completed' ? 1 : 0,
      project_id: projectId,
      created_at: startedAt,
      started_at: startedAt,
      finished_at: finished ? nowIso() : null,
      error: null,
      result_ids: options.resultIds ?? {},
    }
    this.jobs.set(job.id, job)
    return job
  }

  updateJob(id: string, patch: Partial<Job>): Job {
    const job = { ...this.job(id), ...patch }
    this.jobs.set(id, job)
    return job
  }

  async storeBytes(input: { id?: string; filename: string; mimeType: string; bytes: Uint8Array<ArrayBuffer> }): Promise<ArtifactRecord> {
    const record: ArtifactRecord = {
      id: input.id ?? prefixedId('art'),
      filename: input.filename,
      mimeType: input.mimeType,
      sha256: await sha256Hex(input.bytes),
      sizeBytes: input.bytes.byteLength,
      body: { kind: 'bytes', bytes: input.bytes },
    }
    this.artifacts.set(record.id, record)
    return record
  }

  storeArtifact(record: ArtifactRecord): ArtifactRecord {
    this.artifacts.set(record.id, record)
    return record
  }

  indexAudit(record: AuditRecord): void {
    this.audits.set(record.run.id, record)
    for (const issue of record.issues) this.issueAudits.set(issue.id, record.run.id)
  }
}

export function exportArtifact(format: string, record: ArtifactRecord): ExportArtifact {
  return {
    format,
    artifact_id: record.id,
    sha256: record.sha256,
    size_bytes: record.sizeBytes,
    mime_type: record.mimeType,
    download_url: `${API_BASE}/artifacts/${record.id}/download`,
  }
}

function required<T>(value: T | undefined, entity: string, id: string): T {
  if (value === undefined) throw notFound(entity, id)
  return value
}
