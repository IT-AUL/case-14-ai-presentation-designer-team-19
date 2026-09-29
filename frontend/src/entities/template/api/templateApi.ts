import { useQuery } from '@tanstack/react-query'
import { api, unwrap, uploadWithProgress } from '@/shared/api'
import { API_BASE } from '@/shared/config'
import type { TemplateAsset } from '../model/types'

export const templateKeys = {
  detail: (templateId: string) => ['template', templateId] as const,
  dna: (templateId: string) => ['template', templateId, 'dna'] as const,
}

export function useTemplate(templateId: string | null | undefined) {
  return useQuery({
    queryKey: templateKeys.detail(templateId ?? ''),
    queryFn: () => unwrap(api.GET('/api/v1/templates/{template_id}', { params: { path: { template_id: templateId as string } } })),
    enabled: Boolean(templateId),
  })
}

export function useDesignDna(templateId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: templateKeys.dna(templateId ?? ''),
    queryFn: () => unwrap(api.GET('/api/v1/templates/{template_id}/design-dna', { params: { path: { template_id: templateId as string } } })),
    enabled: Boolean(templateId) && enabled,
  })
}

export function uploadTemplate(projectId: string, file: File, onProgress?: (fraction: number) => void): Promise<TemplateAsset> {
  const form = new FormData()
  form.append('file', file, file.name)
  return uploadWithProgress<TemplateAsset>({ url: `${API_BASE}/projects/${encodeURIComponent(projectId)}/templates`, form, onProgress })
}

export function analyzeTemplate(templateId: string) {
  return unwrap(api.POST('/api/v1/templates/{template_id}/analyze', { params: { path: { template_id: templateId } }, body: {} }))
}
