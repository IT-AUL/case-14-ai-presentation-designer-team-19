import type {
  PassportAutoFix,
  PassportExport,
  PassportFallback,
  PassportIssues,
  PassportModelProfile,
  PassportScore,
  PassportStageTiming,
  QualityPassport,
} from '../model/types'

type Json = Record<string, unknown>

export class PassportFormatError extends Error {
  constructor() {
    super('Паспорт качества пришёл в неизвестном формате')
    this.name = 'PassportFormatError'
  }
}

function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function section(source: Json, key: string): Json {
  const value = source[key]
  return isRecord(value) ? value : {}
}

function text(source: Json, key: string): string | null {
  const value = source[key]
  return typeof value === 'string' && value.length > 0 ? value : null
}

function number(source: Json, key: string): number | null {
  const value = source[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function flag(source: Json, key: string): boolean | null {
  const value = source[key]
  return typeof value === 'boolean' ? value : null
}

function stringRecord(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {}
  const entries = Object.entries(value).flatMap(([key, item]) =>
    typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean' ? [[key, String(item)] as const] : [],
  )
  return Object.fromEntries(entries)
}

function scores(value: unknown): PassportScore[] | null {
  if (!isRecord(value)) return null
  const list = Object.entries(value).flatMap(([key, item]) => (typeof item === 'number' && Number.isFinite(item) ? [{ key, value: item }] : []))
  return list.length > 0 ? list : null
}

function stageTimings(value: unknown): PassportStageTiming[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (!isRecord(item)) return []
      const stage = text(item, 'stage') ?? text(item, 'name')
      const seconds = number(item, 'seconds') ?? number(item, 'duration_seconds')
      return stage && seconds !== null ? [{ stage, seconds }] : []
    })
  }
  return (scores(value) ?? []).map(({ key, value: seconds }) => ({ stage: key, seconds }))
}

function isAutoFix(item: unknown): boolean {
  return isRecord(item) && item.strategy === 'auto_fix'
}

function autoFixes(value: unknown): PassportAutoFix[] {
  if (!Array.isArray(value)) return []
  return value.filter(isAutoFix).flatMap((item): PassportAutoFix[] => {
    if (!isRecord(item)) return []
    const feature = text(item, 'feature') ?? text(item, 'rule_code')
    if (!feature) return []
    const disclosure = text(item, 'disclosure')
    const count = disclosure ? Number(/^\s*(\d+)/.exec(disclosure)?.[1]) : NaN
    return [{ ruleCode: feature.replace(/^repair\./, ''), count: Number.isFinite(count) ? count : null, detail: disclosure }]
  })
}

function fallbacks(value: unknown): PassportFallback[] {
  if (!Array.isArray(value)) return []
  return value.filter((item) => !isAutoFix(item)).flatMap((item): PassportFallback[] => {
    if (typeof item === 'string') return item ? [{ label: item, detail: null }] : []
    if (!isRecord(item)) return []
    const label = text(item, 'stage') ?? text(item, 'component') ?? text(item, 'kind') ?? text(item, 'code') ?? text(item, 'rule_code') ?? text(item, 'feature')
    const detail =
      text(item, 'disclosure') ??
      text(item, 'reason') ??
      text(item, 'message') ??
      text(item, 'detail') ??
      text(item, 'description') ??
      text(item, 'summary') ??
      text(item, 'action')
    if (label) return [{ label, detail }]
    return detail ? [{ label: detail, detail: null }] : []
  })
}

function modelProfiles(value: unknown): PassportModelProfile[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item): PassportModelProfile[] => {
    if (typeof item === 'string') return [{ role: null, modelId: item, attributes: {} }]
    if (!isRecord(item)) return []
    const attributes = stringRecord(item)
    return [{ role: attributes.role ?? null, modelId: attributes.model_id ?? attributes.model ?? null, attributes }]
  })
}

function exportsOf(value: unknown): PassportExport[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item): PassportExport[] => {
    if (!isRecord(item)) return []
    const format = text(item, 'format')
    const artifactId = text(item, 'artifact_id')
    return format && artifactId ? [{ format, artifactId, sha256: text(item, 'sha256'), sizeBytes: number(item, 'size_bytes') }] : []
  })
}

function issues(source: Json): PassportIssues {
  return {
    blocker: number(source, 'blocker') ?? 0,
    error: number(source, 'error') ?? 0,
    warning: number(source, 'warning') ?? 0,
    info: number(source, 'info') ?? 0,
    unresolved: number(source, 'unresolved'),
  }
}

export function parsePassport(raw: unknown): QualityPassport {
  if (!isRecord(raw) || !isRecord(raw.metrics)) throw new PassportFormatError()
  const inputs = section(raw, 'inputs')
  const metrics = section(raw, 'metrics')
  const validity = section(metrics, 'validity')
  const editability = section(metrics, 'editability')
  const contentSupport = section(metrics, 'content_support')
  const readability = section(metrics, 'readability')
  const timings = section(metrics, 'timings')
  const provenance = section(raw, 'provenance')

  return {
    schemaVersion: text(raw, 'schema_version'),
    runId: text(raw, 'run_id'),
    generatedAt: text(raw, 'generated_at'),
    inputs: {
      templateName: text(inputs, 'template_name'),
      templateSha256: text(inputs, 'template_sha256'),
      contentPackId: text(inputs, 'content_pack_id'),
      briefHash: text(inputs, 'brief_hash'),
    },
    validity: {
      opensCleanly: flag(validity, 'opens_cleanly'),
      ooxmlErrors: number(validity, 'ooxml_errors'),
      roundTripOk: flag(validity, 'round_trip_ok'),
    },
    editability: {
      peiLevel: number(editability, 'pei_level'),
      nativeTextRatio: number(editability, 'native_text_ratio'),
      rasterOnlySlides: number(editability, 'raster_only_slides'),
    },
    styleFidelity: scores(metrics.style_fidelity),
    contentSupport: {
      supportedClaims: number(contentSupport, 'supported_claims'),
      unsupportedClaims: number(contentSupport, 'unsupported_claims'),
      numbersVerified: number(contentSupport, 'numbers_verified'),
      numbersFailed: number(contentSupport, 'numbers_failed'),
    },
    readability: {
      contrastFailures: number(readability, 'contrast_failures'),
      overflowCount: number(readability, 'overflow_count'),
      avgOccupancy: number(readability, 'avg_occupancy'),
    },
    timings: {
      totalSeconds: number(timings, 'total_seconds'),
      perStage: stageTimings(timings.per_stage),
    },
    usage: scores(metrics.usage),
    issues: issues(section(raw, 'issues_summary')),
    fallbacks: fallbacks(raw.fallbacks),
    autoFixes: autoFixes([...(Array.isArray(raw.fallbacks) ? raw.fallbacks : []), ...(Array.isArray(raw.auto_fixes) ? raw.auto_fixes.map((item) => (isRecord(item) ? { strategy: 'auto_fix', ...item } : item)) : [])]),
    provenance: {
      pipelineVersion: text(provenance, 'pipeline_version'),
      skillVersion: text(provenance, 'skill_version'),
      promptVersions: stringRecord(provenance.prompt_versions),
      modelProfiles: modelProfiles(provenance.model_profiles),
      configHashes: stringRecord(provenance.config_hashes),
    },
    exports: exportsOf(raw.exports),
  }
}
