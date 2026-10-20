import { useMutation, useQueryClient } from '@tanstack/react-query'
import { assistantKeys, type BrandbookSummary, type BrandPriority, type JsonRecord } from '@/entities/assistant'
import { getActiveProviderSession } from '@/entities/provider-session'
import { api, isUnsupportedEndpoint, unwrap } from '@/shared/api'

function toRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as JsonRecord) : {}
}

function toText(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function toRecords(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter((item): item is JsonRecord => item !== null && typeof item === 'object' && !Array.isArray(item)) : []
}

function toStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

export function normalizeAnalyzedBrandbook(value: unknown): BrandbookSummary {
  const r = toRecord(value)
  const rawPalette = Array.isArray(r.palette) ? r.palette : Array.isArray(r.colors) ? r.colors : []
  return {
    ...r,
    id: toText(r.id) ?? toText(r.brandbook_id),
    name: toText(r.name),
    palette: toRecords(rawPalette).map((c) => ({
      ...c,
      name: toText(c.name),
      hex: toText(c.hex),
      role: toText(c.role),
      source: toText(c.source),
    })),
    fonts: toRecords(r.fonts).map((f) => ({
      ...f,
      role: toText(f.role),
      family: toText(f.family),
      source: toText(f.source),
    })),
    logos: toRecords(r.logos).map((l) => ({
      ...l,
      file_name: toText(l.file_name),
      artifact_id: toText(l.artifact_id),
    })),
    rules: Array.isArray(r.rules) ? r.rules : [],
    sources: Array.isArray(r.sources) ? r.sources : [],
    warnings: toStrings(r.warnings),
    conflicts: toRecords(r.conflicts).map((c) => ({
      ...c,
      field: toText(c.field),
      kind: toText(c.kind),
      template: toText(c.template) ?? toText(c.template_value),
      brandbook: toText(c.brandbook) ?? toText(c.brandbook_value),
      resolved: toText(c.resolved) ?? toText(c.winner),
      note: toText(c.note),
    })),
    priority: r.priority === 'template' || r.priority === 'brandbook' ? r.priority : null,
    assistant_note: toText(r.assistant_note),
  }
}

export interface AnalyzeBrandbookInput {
  brandbookId: string
  priority?: BrandPriority | null
  force?: boolean
}

export function useAnalyzeBrandbook() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ brandbookId, priority, force }: AnalyzeBrandbookInput): Promise<BrandbookSummary | null> => {
      if (!brandbookId) return null
      const activeSession = getActiveProviderSession()
      const patch = (body: Record<string, unknown>) =>
        unwrap(
          api.PATCH('/api/v1/brandbooks/{brandbook_id}', {
            params: { path: { brandbook_id: brandbookId } },
            body: body as { priority: 'brandbook' | 'template' },
          }),
        )
      try {
        const raw = await patch({
          analyze: true,
          ...(priority ? { priority } : {}),
          ...(force ? { force: true } : {}),
          ...(activeSession?.id ? { provider_session_id: activeSession.id, use_llm: true } : {}),
        })
        return normalizeAnalyzedBrandbook(raw)
      } catch (error) {
        if (isUnsupportedEndpoint(error)) return null
        try {
          const raw = await patch({ analyze: false, ...(priority ? { priority } : {}), ...(force ? { force: true } : {}) })
          return normalizeAnalyzedBrandbook(raw)
        } catch (retryError) {
          if (isUnsupportedEndpoint(retryError)) return null
          throw retryError
        }
      }
    },
    onSuccess: (data, { brandbookId }) => {
      if (!data) return
      client.setQueryData(assistantKeys.brandbookSummary(brandbookId), data)
      void client.invalidateQueries({ queryKey: assistantKeys.sessions })
      void client.invalidateQueries({ queryKey: ['assistant', 'style-preview'] })
    },
  })
}
